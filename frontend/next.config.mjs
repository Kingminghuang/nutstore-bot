/** @type {import('next').NextConfig} */
const lifecycleEvent = process.env.npm_lifecycle_event
const enableDevIndicators = lifecycleEvent === "dev" || lifecycleEvent === "tauri"

const nextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: false,
  },
  images: {
    unoptimized: true,
  },
  devIndicators: enableDevIndicators ? { position: "bottom-left" } : false,
}

export default nextConfig
