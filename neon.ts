import { defineConfig } from "@neon/config/v1";

export default defineConfig({
  preview: {
    functions: {
      ebayDeletion: {
        name: "Finder eBay account deletion",
        source: "./functions/ebay-deletion.ts",
        env: {
          EBAY_DELETION_ENDPOINT_URL: process.env.EBAY_DELETION_ENDPOINT_URL ?? "",
          EBAY_DELETION_VERIFICATION_TOKEN:
            process.env.EBAY_DELETION_VERIFICATION_TOKEN ?? "",
          EBAY_PRODUCTION_CLIENT_ID: process.env.EBAY_PRODUCTION_CLIENT_ID ?? "",
          EBAY_PRODUCTION_CLIENT_SECRET: process.env.EBAY_PRODUCTION_CLIENT_SECRET ?? "",
        },
      },
    },
  },
});
