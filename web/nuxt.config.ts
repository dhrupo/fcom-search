export default defineNuxtConfig({
  compatibilityDate: "2026-08-01",
  ssr: false,
  modules: ["@nuxtjs/tailwindcss"],
  css: ["~/assets/css/main.css"],
  app: { head: { title: "fcommerce-search", htmlAttrs: { lang: "en" } } },
  // Baked in by `nuxi generate` (NUXT_PUBLIC_TURNSTILE_SITE_KEY); Cloudflare's always-pass test key otherwise.
  runtimeConfig: { public: { turnstileSiteKey: "1x00000000000000000000AA" } },
});
