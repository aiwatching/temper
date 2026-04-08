// Verify the correct platform binary is available
const platform = `${process.platform}-${process.arch}`;
const PLATFORMS = {
  "darwin-arm64": "@aion0/temper-darwin-arm64",
  "darwin-x64": "@aion0/temper-darwin-x64",
  "linux-x64": "@aion0/temper-linux-x64",
};

const pkg = PLATFORMS[platform];
if (!pkg) {
  console.warn(`[temper] Warning: no prebuilt binary for ${platform}`);
  console.warn(`[temper] Supported platforms: ${Object.keys(PLATFORMS).join(", ")}`);
  process.exit(0); // Don't fail install
}

try {
  require.resolve(`${pkg}/package.json`);
  console.log(`[temper] Platform binary installed: ${pkg}`);
} catch {
  console.warn(`[temper] Platform binary not found: ${pkg}`);
  console.warn(`[temper] Try: npm install ${pkg}`);
}
