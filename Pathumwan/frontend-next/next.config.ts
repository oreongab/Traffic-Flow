import type { NextConfig } from "next";

const backendInternalUrl = (process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:5000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendInternalUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
