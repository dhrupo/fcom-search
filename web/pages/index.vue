<script setup lang="ts">
interface Place { name: string; country?: string; lat: number; lon: number }
interface Listing {
  item_id: string; title: string; price: number | null; price_text: string | null; town: string | null;
  created_at: number | null; photo: string | null; permalink: string; distance_km: number | null;
}

const keyword = ref("");
const placeText = ref("");
const place = ref<Place | null>(null);
const suggestions = ref<Place[]>([]);
const active = ref(-1);
const radius = ref(40);
const minPrice = ref("");
const maxPrice = ref("");
const days = ref<number | null>(null);
const cities = ref<{ name: string; status: string; found: number }[]>([]);
const results = ref<Listing[]>([]);
const phase = ref<"idle" | "searching" | "done" | "error">("idle");
const locating = ref(false);
const searchId = ref<string | null>(null);
const canLoadMore = ref(false);
const basicMode = ref(false);
const loadingMore = ref(false);
const problem = ref<"" | "human" | "slow">("");
const token = ref("");
const turnstileBox = ref<HTMLElement | null>(null);
let widgetId: string | undefined;
const siteKey = useRuntimeConfig().public.turnstileSiteKey as string;

onMounted(() => {
  const script = document.createElement("script");
  script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
  script.async = true;
  script.onload = () => {
    widgetId = (window as any).turnstile.render(turnstileBox.value, { sitekey: siteKey, size: "flexible",
      callback: (t: string) => { token.value = t; }, "expired-callback": () => { token.value = ""; } });
  };
  document.head.appendChild(script);
});

/** A Turnstile token works once: take it and ask the widget for the next one. */
function takeToken(): string {
  const t = token.value;
  token.value = "";
  if (widgetId !== undefined) (window as any).turnstile.reset(widgetId);
  return t;
}

const label = (p: Place) => [p.name, p.country].filter(Boolean).join(", ");

let suggestTimer: ReturnType<typeof setTimeout> | undefined;
let suggestAbort: AbortController | undefined;
watch(placeText, (text) => {
  if (place.value && text === label(place.value)) return;
  place.value = null;
  clearTimeout(suggestTimer);
  suggestAbort?.abort();
  if (text.trim().length < 2) { suggestions.value = []; return; }
  suggestTimer = setTimeout(async () => {
    const abort = (suggestAbort = new AbortController());
    try {
      const res = await fetch(`/api/places?q=${encodeURIComponent(text)}`, { signal: abort.signal });
      if (res.ok) { suggestions.value = await res.json(); active.value = -1; }
    } catch { /* superseded by newer typing */ }
  }, 250);
});

function choose(p: Place) {
  place.value = p;
  placeText.value = label(p);
  suggestions.value = [];
}

function onPlaceKey(e: KeyboardEvent) {
  if (!suggestions.value.length) return;
  if (e.key === "ArrowDown") { active.value = (active.value + 1) % suggestions.value.length; e.preventDefault(); }
  else if (e.key === "ArrowUp") { active.value = (active.value - 1 + suggestions.value.length) % suggestions.value.length; e.preventDefault(); }
  else if (e.key === "Enter" && active.value >= 0) { choose(suggestions.value[active.value]); e.preventDefault(); }
  else if (e.key === "Escape") suggestions.value = [];
}

function useMyLocation() {
  locating.value = true;
  navigator.geolocation.getCurrentPosition(async ({ coords }) => {
    const near = await fetch(`/api/reverse?lat=${coords.latitude}&lon=${coords.longitude}`).then((r) => r.ok ? r.json() : null).catch(() => null);
    choose({ name: near?.name ? `Near ${near.name}` : "My location", country: near?.country, lat: coords.latitude, lon: coords.longitude });
    locating.value = false;
  }, () => { locating.value = false; });
}

const amount = (v: string) => (v.trim() === "" ? null : Number(v.replace(/[^\d.]/g, "")));
let searchAbort: AbortController | undefined;

async function readEvents(res: Response) {
  if (res.status === 403 || res.status === 429) problem.value = res.status === 429 ? "slow" : "human";
  if (!res.ok || !res.body) throw new Error(String(res.status));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines.filter(Boolean)) applySearchEvent(JSON.parse(line));
    if (done) break;
  }
}

async function search() {
  if (!keyword.value.trim() || !place.value || !token.value) return;
  searchAbort?.abort();
  const abort = (searchAbort = new AbortController());
  cities.value = []; results.value = []; phase.value = "searching";
  searchId.value = null; canLoadMore.value = false; basicMode.value = false; problem.value = "";
  try {
    const res = await fetch("/api/search", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: abort.signal,
      body: JSON.stringify({ keyword: keyword.value.trim(), lat: place.value.lat, lon: place.value.lon,
        radius_km: Math.min(100, Math.max(10, Number(radius.value) || 40)), min_price: amount(minPrice.value),
        max_price: amount(maxPrice.value), days: days.value, turnstile: takeToken() }),
    });
    await readEvents(res);
    // Basic mode: one request per city (each must stay under Cloudflare's 10 ms CPU limit).
    for (const city of basicMode.value && phase.value === "searching" ? cities.value.map((c) => c.name) : []) {
      if (abort !== searchAbort) return;
      await readEvents(await fetch("/api/city", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: abort.signal,
        body: JSON.stringify({ search_id: searchId.value, city }),
      }));
    }
    if (abort === searchAbort && phase.value === "searching") throw new Error("stream ended early");
  } catch (e) {
    if (abort === searchAbort) phase.value = "error";
  }
}

async function loadMore() {
  if (!searchId.value || loadingMore.value) return;
  loadingMore.value = true;
  phase.value = "searching";
  problem.value = "";
  const abort = searchAbort;
  try {
    await readEvents(await fetch("/api/more", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: abort?.signal,
      body: JSON.stringify({ search_id: searchId.value }),
    }));
    if (phase.value === "searching") throw new Error("stream ended early");
  } catch {
    if (abort === searchAbort) phase.value = "error";
  } finally {
    loadingMore.value = false;
  }
}

function applySearchEvent(event: any) {
  if (event.type === "mode") basicMode.value = event.mode === "basic";
  if (event.type === "start") {
    cities.value = event.cities.map((name: string) => ({ name, status: "searching", found: 0 }));
    searchId.value = event.search_id ?? null;
  }
  if (event.type === "city") {
    const city = cities.value.find((c) => c.name === event.city);
    if (city) Object.assign(city, { status: event.status, found: (loadingMore.value ? city.found : 0) + event.found });
    results.value.push(...event.results);
  }
  if (event.type === "done") {
    phase.value = "done";
    searchId.value = event.search_id ?? searchId.value;
    canLoadMore.value = Boolean(event.can_load_more);
  }
  if (event.type === "error") { phase.value = "error"; canLoadMore.value = false; }
}

const summary = computed(() => {
  const n = cities.value.length;
  if (phase.value === "searching") return `Searching ${n} ${n === 1 ? "city" : "cities"}… ${results.value.length} results so far`;
  if (phase.value === "done") return `Done: ${n} ${n === 1 ? "city" : "cities"}, ${results.value.length} ${results.value.length === 1 ? "result" : "results"}`;
  return "";
});
</script>

<template>
  <div>
    <form class="grid gap-3 rounded-xl border border-stone-200 bg-white p-4 md:grid-cols-6" @submit.prevent="search">
      <label class="flex min-w-0 flex-col gap-1 text-sm font-medium md:col-span-3">
        What are you looking for?
        <input v-model="keyword" type="search" maxlength="100" placeholder="panjabi, PS5, trekking shoes…"
               class="w-full min-w-0 rounded-lg border border-stone-300 px-3 py-2.5 text-base font-normal focus:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-700/30">
      </label>
      <div class="relative flex min-w-0 flex-col gap-1 text-sm font-medium md:col-span-3">
        <label for="place">Place</label>
        <div class="flex gap-2">
          <input id="place" v-model="placeText" role="combobox" autocomplete="off" aria-autocomplete="list"
                 :aria-expanded="suggestions.length > 0" aria-controls="place-options"
                 :aria-activedescendant="active >= 0 ? `place-${active}` : undefined"
                 placeholder="Type a city, or use your location" @keydown="onPlaceKey"
                 class="min-w-0 flex-1 rounded-lg border border-stone-300 px-3 py-2.5 text-base font-normal focus:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-700/30">
          <button type="button" :disabled="locating" @click="useMyLocation"
                  class="whitespace-nowrap rounded-lg border border-stone-300 px-3 text-sm font-medium hover:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-700/30 disabled:opacity-60">
            {{ locating ? "Locating…" : "Use my location" }}
          </button>
        </div>
        <ul v-if="suggestions.length" id="place-options" role="listbox" aria-label="Place suggestions"
            class="absolute top-full z-10 mt-1 w-full overflow-hidden rounded-lg border border-stone-200 bg-white shadow-lg">
          <li v-for="(s, i) in suggestions" :id="`place-${i}`" :key="`${s.name}-${s.lat}-${s.lon}`" role="option"
              :aria-selected="i === active" class="cursor-pointer px-3 py-2 font-normal hover:bg-teal-50"
              :class="i === active && 'bg-teal-50'" @mousedown.prevent="choose(s)">{{ label(s) }}</li>
        </ul>
      </div>
      <label class="flex min-w-0 flex-col gap-1 text-sm font-medium">
        Radius (km)
        <input v-model.number="radius" type="number" min="10" max="100" step="10"
               class="w-full min-w-0 rounded-lg border border-stone-300 px-3 py-2.5 text-base font-normal focus:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-700/30">
      </label>
      <label class="flex min-w-0 flex-col gap-1 text-sm font-medium">
        Min price
        <input v-model="minPrice" inputmode="numeric" placeholder="Any"
               class="w-full min-w-0 rounded-lg border border-stone-300 px-3 py-2.5 text-base font-normal focus:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-700/30">
      </label>
      <label class="flex min-w-0 flex-col gap-1 text-sm font-medium">
        Max price
        <input v-model="maxPrice" inputmode="numeric" placeholder="Any"
               class="w-full min-w-0 rounded-lg border border-stone-300 px-3 py-2.5 text-base font-normal focus:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-700/30">
      </label>
      <label class="flex min-w-0 flex-col gap-1 text-sm font-medium">
        Posted within
        <select v-model="days" class="w-full min-w-0 rounded-lg border border-stone-300 px-3 py-2.5 text-base font-normal focus:border-teal-700 focus:outline-none focus:ring-2 focus:ring-teal-700/30">
          <option :value="null">Any time</option>
          <option v-for="d in [3, 7, 15, 30, 45, 60]" :key="d" :value="d">Last {{ d }} days</option>
        </select>
      </label>
      <button type="submit" :disabled="!keyword.trim() || !place || !token || phase === 'searching'"
              class="self-end rounded-lg bg-teal-700 px-5 py-2.5 font-medium text-white hover:bg-teal-800 focus:outline-none focus:ring-2 focus:ring-teal-700/40 disabled:opacity-50 md:col-span-2">
        {{ token ? "Search" : "Checking you're human…" }}
      </button>
      <div ref="turnstileBox" class="md:col-span-6" />
    </form>

    <p v-if="phase === 'error'" role="alert" class="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800">
      {{ PROBLEMS[problem] }}
    </p>
    <p v-if="basicMode" class="mt-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900">
      Facebook's map search is not answering right now, so this is basic mode: we searched the big cities nearby by name.
    </p>
    <p role="status" class="mt-6 text-sm text-stone-600">{{ summary }}</p>
    <ul v-if="cities.length" aria-label="Cities" class="mt-2 flex flex-wrap gap-2 text-sm">
      <li v-for="c in cities" :key="c.name" class="rounded-full border border-stone-200 bg-white px-3 py-1">
        <span class="font-medium">{{ c.name }}</span>: {{ CITY_STATUS[c.status]?.(c.found) ?? c.status }}
      </li>
    </ul>

    <ul v-if="results.length" aria-label="Results" class="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
      <li v-for="r in results" :key="r.item_id" class="flex flex-col overflow-hidden rounded-xl border border-stone-200 bg-white">
        <div class="aspect-square bg-stone-100">
          <img v-if="r.photo" :src="r.photo" alt="" loading="lazy" referrerpolicy="no-referrer" class="h-full w-full object-cover">
        </div>
        <div class="flex flex-1 flex-col gap-1 p-3">
          <h3 class="text-sm font-medium leading-snug">{{ r.title }}</h3>
          <p class="text-lg font-semibold" data-testid="price">{{ r.price_text ?? "Price not given" }}</p>
          <p class="text-xs text-stone-500">
            {{ r.town }}<span v-if="r.distance_km != null"> · {{ r.distance_km }} km</span><span v-if="postedAgo(r.created_at)"> · {{ postedAgo(r.created_at) }}</span>
          </p>
          <a :href="r.permalink" target="_blank" rel="noopener noreferrer"
             class="mt-auto pt-2 text-sm font-medium text-teal-800 underline-offset-2 hover:underline">Open on Facebook</a>
        </div>
      </li>
    </ul>
    <div v-if="canLoadMore && results.length" class="mt-6 flex justify-center">
      <button type="button" :disabled="loadingMore" @click="loadMore"
              class="rounded-lg border border-teal-700 px-6 py-2.5 font-medium text-teal-800 hover:bg-teal-50 focus:outline-none focus:ring-2 focus:ring-teal-700/30 disabled:opacity-60">
        {{ loadingMore ? "Loading…" : "Load more" }}
      </button>
    </div>
    <p v-if="!results.length && phase === 'done'" class="mt-10 text-center text-stone-500">
      No listings match. Try a wider radius, fewer words or no price limits.
    </p>
  </div>
</template>
