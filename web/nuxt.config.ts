export default defineNuxtConfig({
  compatibilityDate: "2026-08-01",
  ssr: false,
  modules: ["@nuxtjs/tailwindcss"],
  css: ["~/assets/css/main.css"],
  app: { head: { title: "fcommerce-search", htmlAttrs: { lang: "en" } } },
});
