import type { NextConfig } from "next";

// FastAPI serves out/ with StaticFiles(html=True). trailingSlash makes out/mobile/index.html.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
