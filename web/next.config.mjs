import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
  reactStrictMode: true,
  // Type-checking is executed separately (`node node_modules/typescript/bin/tsc --noEmit`)
  // because this sandbox cannot spawn the build's type-check worker (EPERM on piped stdio).
  typescript: { ignoreBuildErrors: true },
  turbopack: { root: dir },
};

export default nextConfig;
