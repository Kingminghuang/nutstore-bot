/** @type {import('next').NextConfig} */
const lifecycleEvent = process.env.npm_lifecycle_event
const enableDevIndicators = lifecycleEvent === "dev" || lifecycleEvent === "dev:with-sidecar"

const nextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  devIndicators: enableDevIndicators ? { position: "bottom-left" } : false,
}

export default nextConfig
