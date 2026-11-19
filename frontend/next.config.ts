import type { NextConfig } from "next";

// Browser talks only to :3000; Next proxies /api/* to FastAPI (no CORS, cookies stay same-origin)
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const DIST_DIR = process.env.NEXT_BUILD_ISOLATED === "1" ? ".next-validation" : ".next";
const nextConfig: NextConfig = {
  distDir: DIST_DIR,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },
};

export default nextConfig;
