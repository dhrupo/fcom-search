import { expect, test, type Page } from "@playwright/test";
import { BASIC_PORT } from "../playwright.config";

const BASIC = `http://127.0.0.1:${BASIC_PORT}/`;

async function pickPlace(page: Page, typed: string, option: RegExp) {
  await page.getByLabel("Place").fill(typed);
  await page.getByRole("option", { name: option }).click();
}

test("type_pick_place_filter_and_see_cards", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("What are you looking for?").fill("iphone");
  await pickPlace(page, "sylh", /Sylhet, Bangladesh/);
  await page.getByLabel("Radius").fill("20");
  await page.getByLabel("Min price").fill("10000");
  await page.getByLabel("Max price").fill("30000");
  await page.getByLabel("Posted within").selectOption({ label: "Last 7 days" });
  await page.getByRole("button", { name: "Search" }).click();

  await expect(page.getByRole("status")).toContainText("Done: 1 city, 8 results");
  const cards = page.getByRole("listitem").filter({ has: page.getByRole("link", { name: "Open on Facebook" }) });
  await expect(cards).toHaveCount(8);
  for (const price of await cards.getByTestId("price").allTextContents()) {
    const amount = Number(price.replace(/\D/g, ""));
    expect(amount).toBeGreaterThanOrEqual(10000);
    expect(amount).toBeLessThanOrEqual(30000);
  }
  await expect(cards.first().getByRole("link", { name: "Open on Facebook" }))
    .toHaveAttribute("href", /^https:\/\/www\.facebook\.com\/marketplace\/item\//);
});

test("near_me_with_radius", async ({ page, context }) => {
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 24.8949, longitude: 91.8687 });
  await page.goto("/");
  await page.getByLabel("What are you looking for?").fill("iphone");
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByLabel("Place")).toHaveValue(/Sylhet/);
  await page.getByLabel("Radius").fill("10");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("status")).toContainText("Done: 1 city, 23 results");
});

test("basic_mode_searches_city_by_city", async ({ page }) => {
  await page.goto(BASIC);
  await page.getByLabel("What are you looking for?").fill("iphone");
  await pickPlace(page, "sylh", /Sylhet/);
  await page.getByLabel("Radius").fill("100");
  await page.getByRole("button", { name: "Search" }).click();
  const progress = page.getByRole("list", { name: "Cities" });
  await expect(progress.getByRole("listitem").filter({ hasText: "Sylhet" })).toContainText("found");
  await expect(progress.getByRole("listitem").filter({ hasText: "Shillong" })).toContainText("could not be searched");
  await expect(page.getByRole("status")).toContainText("Done:");
});

test("no_results_message", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("What are you looking for?").fill("zzqx");
  await pickPlace(page, "sylh", /Sylhet/);
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText("No listings match")).toBeVisible();
});

test("server_error_message", async ({ page }) => {
  await page.route("**/api/search", (route) => route.fulfill({ status: 500, body: "boom" }));
  await page.goto("/");
  await page.getByLabel("What are you looking for?").fill("iphone");
  await pickPlace(page, "sylh", /Sylhet/);
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("alert")).toContainText("Search failed");
  await expect(page.getByText("No listings match")).toBeHidden();
});

test("title_with_html_renders_as_text", async ({ page }) => {
  const evil = '<img src=x onerror="window.__xss=1">Phone';
  await page.route("**/api/search", (route) => route.fulfill({
    status: 200, contentType: "application/x-ndjson",
    body: [{ type: "start", cities: ["Sylhet"] },
           { type: "city", city: "Sylhet", status: "ok", found: 1, results: [{ item_id: "1", title: evil, price: 100,
             price_text: "BDT100", town: "Sylhet", created_at: 1, photo: null,
             permalink: "https://www.facebook.com/marketplace/item/1/", distance_km: 0 }] },
           { type: "done", total: 1 }].map((e) => JSON.stringify(e)).join("\n") + "\n",
  }));
  await page.goto("/");
  await page.getByLabel("What are you looking for?").fill("phone");
  await pickPlace(page, "sylh", /Sylhet/);
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText(evil)).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
});

test("fast_typing_in_place_box_shows_latest_suggestions", async ({ page }) => {
  await page.route("**/api/places?q=lond*", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.continue();
  });
  await page.goto("/");
  const isLondon = (r: { url(): string }) => r.url().includes("/api/places?q=lond");
  const slowLondon = Promise.race([page.waitForEvent("requestfailed", isLondon), page.waitForEvent("requestfinished", isLondon)]);
  await page.getByLabel("Place").fill("lond");
  await page.waitForRequest((r) => r.url().includes("/api/places?q=lond"));
  await page.getByLabel("Place").fill("pari");
  await expect(page.getByRole("option", { name: "Paris, France", exact: true })).toBeVisible();
  await slowLondon;
  await expect(page.getByRole("option", { name: "Paris, France", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: /^London,/ })).toHaveCount(0);
});

test("phone_layout_fits", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  const form = await page.locator("form").boundingBox();
  for (const field of await page.locator("form input, form select, form button").all()) {
    const box = await field.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(form!.x + form!.width);
  }
  const logo = await page.getByText("fcommerce-search", { exact: true }).boundingBox();
  expect(logo!.height).toBeLessThan(40);
});

test("repeat_search_counts_what_it_shows", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("What are you looking for?").fill("iphone");
  await pickPlace(page, "sylh", /Sylhet/);
  await page.getByLabel("Radius").fill("10");
  for (let i = 0; i < 2; i++) {
    await page.getByRole("button", { name: "Search" }).click();
    await expect(page.getByRole("status")).toContainText("Done: 1 city, 23 results");
    await expect(page.getByRole("list", { name: "Cities" })).toContainText("Sylhet: 23 found");
  }
});


test("load_more_adds_new_cards", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("What are you looking for?").fill("iphone");
  await pickPlace(page, "sylh", /Sylhet/);
  await page.getByRole("button", { name: "Search" }).click();
  const cards = page.getByRole("list", { name: "Results" }).getByRole("listitem");
  await expect(page.getByRole("status")).toContainText("Done: 1 city, 23 results");
  const loadMore = page.getByRole("button", { name: "Load more" });
  for (let click = 0; click < 3 && (await cards.count()) === 23; click++) {
    await loadMore.click();
    await expect(page.getByRole("status")).toContainText(/Done/);
  }
  const after = await cards.count();
  expect(after).toBeGreaterThan(23);
  const links = await cards.getByRole("link", { name: "Open on Facebook" }).evaluateAll((a) => a.map((x) => x.getAttribute("href")));
  expect(new Set(links).size).toBe(links.length);
});

test("basic_mode_notice", async ({ page }) => {
  await page.goto(BASIC);
  await page.getByLabel("What are you looking for?").fill("iphone");
  await pickPlace(page, "sylh", /Sylhet/);
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText(/basic mode/i)).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Done:");
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
});
