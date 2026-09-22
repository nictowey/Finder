import { defineConfig } from "@neon/config/v1";

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} before deploying the eBay endpoint.`);
  return value;
}

export default defineConfig({
  functions: {
    ebaydeletion: {
      name: "Finder eBay account deletion",
      source: "./functions/ebay-deletion.ts",
      env: {
        EBAY_DELETION_ENDPOINT_URL: requiredEnv("EBAY_DELETION_ENDPOINT_URL"),
        EBAY_DELETION_VERIFICATION_TOKEN: requiredEnv("EBAY_DELETION_VERIFICATION_TOKEN"),
        EBAY_PRODUCTION_CLIENT_ID: requiredEnv("EBAY_PRODUCTION_CLIENT_ID"),
        EBAY_PRODUCTION_CLIENT_SECRET: requiredEnv("EBAY_PRODUCTION_CLIENT_SECRET"),
      },
    },
  },
});
