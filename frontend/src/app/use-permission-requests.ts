import { useCallback, useRef, useState } from "react"

import {
  type LiveTurnStateBySession,
  type PermissionRequestOutcome,
  updateLiveTurnBySession,
} from "@/app/live-turn-state"
import type { PendingPermissionRequest } from "@/features/session"

export function usePermissionRequests({
  setLiveTurnBySession,
}: {
  setLiveTurnBySession: React.Dispatch<React.SetStateAction<LiveTurnStateBySession>>
}) {
  const [pendingPermissionRequest, setPendingPermissionRequest] =
    useState<PendingPermissionRequest | null>(null)
  const pendingPermissionRequestRef = useRef<PendingPermissionRequest | null>(null)
  const permissionResolverRef = useRef<((outcome: PermissionRequestOutcome) => void) | null>(null)

  const resolvePermissionRequest = useCallback(
    (outcome: PermissionRequestOutcome, sessionId?: string) => {
      const targetSessionId = sessionId ?? pendingPermissionRequestRef.current?.sessionId ?? null
      if (targetSessionId) {
        setLiveTurnBySession((prev) =>
          updateLiveTurnBySession(prev, targetSessionId, (liveTurn) => ({
            ...liveTurn,
            waitingForPermission: false,
          }))
        )
      }

      pendingPermissionRequestRef.current = null
      setPendingPermissionRequest(null)

      const resolver = permissionResolverRef.current
      permissionResolverRef.current = null
      resolver?.(outcome)
    },
    [setLiveTurnBySession]
  )

  const requestPermissionFromUser = useCallback(
    (request: PendingPermissionRequest) => {
      if (permissionResolverRef.current) {
        resolvePermissionRequest({ outcome: { outcome: "cancelled" } })
      }

      pendingPermissionRequestRef.current = request
      setPendingPermissionRequest(request)
      setLiveTurnBySession((prev) =>
        updateLiveTurnBySession(prev, request.sessionId, (liveTurn) => ({
          ...liveTurn,
          waitingForPermission: true,
        }))
      )

      return new Promise<PermissionRequestOutcome>((resolve) => {
        permissionResolverRef.current = resolve
      })
    },
    [resolvePermissionRequest, setLiveTurnBySession]
  )

  const cancelAllPendingPermissionRequests = useCallback(() => {
    if (permissionResolverRef.current) {
      resolvePermissionRequest({ outcome: { outcome: "cancelled" } })
    }
  }, [resolvePermissionRequest])

  const cancelPendingPermissionRequestForSession = useCallback(
    (sessionId: string) => {
      if (pendingPermissionRequestRef.current?.sessionId === sessionId) {
        resolvePermissionRequest({ outcome: { outcome: "cancelled" } }, sessionId)
      }
    },
    [resolvePermissionRequest]
  )

  const hasPendingPermissionRequestForSession = useCallback((sessionId: string) => {
    return pendingPermissionRequestRef.current?.sessionId === sessionId
  }, [])

  const getPendingPermissionOptionId = useCallback(
    (kind: "allow_once" | "allow_always" | "reject_once", fallback: string) => {
      return pendingPermissionRequest?.options.find((option) => option.kind === kind)?.optionId ?? fallback
    },
    [pendingPermissionRequest]
  )

  return {
    pendingPermissionRequest,
    resolvePermissionRequest,
    requestPermissionFromUser,
    cancelAllPendingPermissionRequests,
    cancelPendingPermissionRequestForSession,
    hasPendingPermissionRequestForSession,
    getPendingPermissionOptionId,
  }
}
