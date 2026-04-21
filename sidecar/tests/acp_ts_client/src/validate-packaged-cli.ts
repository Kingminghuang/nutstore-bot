import fs from "node:fs";
import path from "node:path";
import { once } from "node:events";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { Readable, Transform, Writable } from "node:stream";

import * as acp from "@agentclientprotocol/sdk";

type Args = {
  binPath: string;
  nsBotHome: string;
  workspace: string;
  providerId: string;
  modelId: string;
  baseUrl: string;
  wireLogFile: string;
  stderrLogFile: string;
};

type WireDirection = "client->agent" | "agent->client";

type WireEntry = {
  timestamp: string;
  direction: WireDirection;
  id?: string | number;
  method?: string;
  sessionId?: string;
  payload: unknown;
};

function parseArgs(argv: string[]): Args {
  const parsed = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      throw new Error(`unexpected argument: ${token}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`missing value for ${token}`);
    }
    parsed.set(token.slice(2), value);
    index += 1;
  }

  const args = {
    binPath: parsed.get("bin-path") ?? "",
    nsBotHome: parsed.get("ns-bot-home") ?? "",
    workspace: parsed.get("workspace") ?? "",
    providerId: parsed.get("provider-id") ?? "",
    modelId: parsed.get("model-id") ?? "",
    baseUrl: parsed.get("base-url") ?? "",
    wireLogFile: parsed.get("wire-log-file") ?? "",
    stderrLogFile: parsed.get("stderr-log-file") ?? "",
  } satisfies Args;

  for (const [key, value] of Object.entries(args)) {
    if (!value) {
      throw new Error(`missing required argument: ${key}`);
    }
  }
  return args;
}

function ensureParentDir(filePath: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function appendWireLog(logStream: fs.WriteStream, direction: WireDirection, line: string): void {
  const trimmed = line.trim();
  if (!trimmed) {
    return;
  }

  let payload: unknown = trimmed;
  let method: string | undefined;
  let id: string | number | undefined;
  let sessionId: string | undefined;

  try {
    payload = JSON.parse(trimmed) as Record<string, unknown>;
    if (payload && typeof payload === "object") {
      const record = payload as Record<string, unknown>;
      method = typeof record.method === "string" ? record.method : undefined;
      id = typeof record.id === "string" || typeof record.id === "number" ? record.id : undefined;
      const params = record.params;
      if (params && typeof params === "object") {
        const paramsRecord = params as Record<string, unknown>;
        sessionId = typeof paramsRecord.sessionId === "string" ? paramsRecord.sessionId : undefined;
      }
    }
  } catch {
    payload = trimmed;
  }

  const entry: WireEntry = {
    timestamp: new Date().toISOString(),
    direction,
    payload,
  };
  if (method) {
    entry.method = method;
  }
  if (id !== undefined) {
    entry.id = id;
  }
  if (sessionId) {
    entry.sessionId = sessionId;
  }
  logStream.write(`${JSON.stringify(entry)}\n`);
}

class LineLoggingTransform extends Transform {
  private buffer = "";

  constructor(
    private readonly direction: WireDirection,
    private readonly logStream: fs.WriteStream,
  ) {
    super();
  }

  override _transform(chunk: Buffer, _encoding: BufferEncoding, callback: (error?: Error | null) => void): void {
    const text = chunk.toString("utf-8");
    this.buffer += text;

    let newlineIndex = this.buffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = this.buffer.slice(0, newlineIndex);
      appendWireLog(this.logStream, this.direction, line);
      this.buffer = this.buffer.slice(newlineIndex + 1);
      newlineIndex = this.buffer.indexOf("\n");
    }

    this.push(chunk);
    callback();
  }

  override _flush(callback: (error?: Error | null) => void): void {
    if (this.buffer.trim()) {
      appendWireLog(this.logStream, this.direction, this.buffer);
    }
    this.buffer = "";
    callback();
  }
}

class RecordingClient implements acp.Client {
  readonly sessionUpdates: Array<{ sessionId: string; update: Record<string, unknown> }> = [];

  async requestPermission(params: acp.RequestPermissionRequest): Promise<acp.RequestPermissionResponse> {
    const firstOption = params.options[0];
    if (!firstOption) {
      throw new Error("requestPermission called without options");
    }
    return {
      outcome: {
        outcome: "selected",
        optionId: firstOption.optionId,
      },
    };
  }

  async sessionUpdate(params: acp.SessionNotification): Promise<void> {
    const updatePayload = params.update as unknown as Record<string, unknown>;
    this.sessionUpdates.push({
      sessionId: params.sessionId,
      update: updatePayload,
    });
  }

  async writeTextFile(_params: acp.WriteTextFileRequest): Promise<acp.WriteTextFileResponse> {
    return {};
  }

  async readTextFile(_params: acp.ReadTextFileRequest): Promise<acp.ReadTextFileResponse> {
    return { content: "" };
  }
}

function assertCondition(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

async function callNsbotExtMethod(
  connection: acp.ClientSideConnection,
  method: string,
  params: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return await connection.extMethod(`_${method}`, params);
}

async function terminateChild(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return;
  }

  child.kill("SIGTERM");
  const exitPromise = once(child, "exit").then(() => undefined);
  const timeoutPromise = new Promise<void>((resolve) => {
    setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
      }
      resolve();
    }, 5000);
  });
  await Promise.race([exitPromise, timeoutPromise]);
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  ensureParentDir(args.wireLogFile);
  ensureParentDir(args.stderrLogFile);
  const wireLogStream = fs.createWriteStream(args.wireLogFile, { flags: "a" });
  const stderrLogStream = fs.createWriteStream(args.stderrLogFile, { flags: "a" });

  const child = spawn(args.binPath, ["--ns-bot-home", args.nsBotHome, "--acp"], {
    cwd: args.workspace,
    stdio: ["pipe", "pipe", "pipe"],
    env: process.env,
  });

  child.stderr.pipe(stderrLogStream);

  const outboundTap = new LineLoggingTransform("client->agent", wireLogStream);
  const inboundTap = new LineLoggingTransform("agent->client", wireLogStream);
  outboundTap.pipe(child.stdin);
  child.stdout.pipe(inboundTap);

  const input = Writable.toWeb(outboundTap);
  const output = Readable.toWeb(inboundTap) as ReadableStream<Uint8Array>;
  const stream = acp.ndJsonStream(input, output);
  const client = new RecordingClient();
  const connection = new acp.ClientSideConnection((_agent: unknown) => client, stream);

  try {
    const initResult = await connection.initialize({
      protocolVersion: acp.PROTOCOL_VERSION,
      clientCapabilities: {
        fs: {
          readTextFile: false,
          writeTextFile: false,
        },
        terminal: false,
      },
      clientInfo: {
        name: "nsbot-acp-ts-e2e",
        title: "NSBot ACP TS E2E",
        version: "0.1.0",
      },
    });

    assertCondition(initResult.protocolVersion === 1, `unexpected protocol version: ${initResult.protocolVersion}`);
    const authMethodIds = new Set(
      (initResult.authMethods ?? []).map((item: { id?: unknown }) => String(item.id ?? "")),
    );
    assertCondition(authMethodIds.size > 0, "initialize did not advertise any auth methods");

    const catalogResult = await callNsbotExtMethod(connection, "nsbot/provider/catalog", {});
    const catalogProviders = Array.isArray((catalogResult as Record<string, unknown>).providers)
      ? ((catalogResult as Record<string, unknown>).providers as Array<Record<string, unknown>>)
      : [];
    assertCondition(
      catalogProviders.some((provider) => String(provider.id ?? "") === "custom") ||
        catalogProviders.some((provider) => String(provider.id ?? "") === "openai"),
      "provider catalog did not expose expected catalog entries",
    );

    const modelOptions = await callNsbotExtMethod(connection, "nsbot/provider/model_options", {});
    const defaultSelection = (modelOptions as Record<string, unknown>).defaultSelection as Record<string, unknown> | undefined;
    assertCondition(defaultSelection, "provider/model_options did not return defaultSelection");
    assertCondition(String(defaultSelection.providerId ?? "") === args.providerId, "defaultSelection providerId mismatch");
    assertCondition(String(defaultSelection.modelId ?? "") === args.modelId, "defaultSelection modelId mismatch");

    const normalizedMethodId = `USE_${args.providerId.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase()}`;
    let authenticatedMethodId = normalizedMethodId;
    if (authMethodIds.has(normalizedMethodId)) {
      await connection.authenticate({ methodId: normalizedMethodId });
    } else {
      assertCondition(authMethodIds.has("GATEWAY"), "initialize did not advertise a usable auth method for the configured provider");
      authenticatedMethodId = "GATEWAY";
      await connection.authenticate({
        methodId: "GATEWAY",
        _meta: {
          gateway: {
            protocol: "custom",
            baseUrl: args.baseUrl,
          },
        },
      });
    }

    const sessionResult = await connection.newSession({
      cwd: args.workspace,
      mcpServers: [],
    });
    assertCondition(sessionResult.sessionId, "newSession did not return a sessionId");

    const loadResult = await connection.loadSession({
      cwd: args.workspace,
      sessionId: sessionResult.sessionId,
      mcpServers: [],
    });
    assertCondition(Array.isArray(loadResult.configOptions), "loadSession did not return configOptions");

    let promptStopReason: string | null = null;
    let promptError: string | null = null;
    try {
      const promptResult = await connection.prompt({
        sessionId: sessionResult.sessionId,
        prompt: [
          {
            type: "text",
            text: "Reply with the single token READY.",
          },
        ],
      });
      assertCondition(
        typeof promptResult.stopReason === "string" && promptResult.stopReason.length > 0,
        "prompt did not return stopReason",
      );
      promptStopReason = promptResult.stopReason;
    } catch (error) {
      promptError = error instanceof Error ? error.message : String(error);
    }

    assertCondition(
      promptStopReason !== null || promptError !== null,
      "prompt neither completed nor returned an ACP error",
    );
    assertCondition(client.sessionUpdates.length > 0, "prompt did not emit any session updates");

    const timelineResult = await callNsbotExtMethod(connection, "nsbot/timeline/list", {
      sessionId: sessionResult.sessionId,
      limit: 20,
    });
    const events = Array.isArray((timelineResult as Record<string, unknown>).events)
      ? ((timelineResult as Record<string, unknown>).events as unknown[])
      : [];
    assertCondition(events.length > 0, "timeline/list did not return any events");

    const summary = {
      protocolVersion: initResult.protocolVersion,
      authenticatedMethodId,
      sessionId: sessionResult.sessionId,
      stopReason: promptStopReason,
      promptError,
      defaultSelection,
      timelineEventCount: events.length,
      sessionUpdateCount: client.sessionUpdates.length,
      wireLogFile: args.wireLogFile,
      stderrLogFile: args.stderrLogFile,
    };
    process.stdout.write(`${JSON.stringify(summary)}\n`);
  } finally {
    outboundTap.end();
    inboundTap.end();
    await terminateChild(child);
    wireLogStream.end();
    stderrLogStream.end();
  }
}

main().catch((error) => {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});