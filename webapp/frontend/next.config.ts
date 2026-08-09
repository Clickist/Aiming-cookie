import type { NextConfig } from "next";

const staticExport = process.env.AIMING_COOKIE_STATIC_EXPORT === "1";

const staticConfig: NextConfig = {
  reactStrictMode: true,
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

const serverConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (process.env.AIMING_COOKIE_API_MODE === "mock") return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

const nextConfig: NextConfig = staticExport ? staticConfig : serverConfig;

export default nextConfig;
