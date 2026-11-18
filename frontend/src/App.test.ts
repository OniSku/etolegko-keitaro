import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, type App as VueApp } from "vue";
import App from "./App.vue";

let host: HTMLDivElement;
let app: VueApp;
const stream = {
  id: 502,
  name: "Offers",
  schema: "landings",
  revision: 1,
  dirty: false,
  total: 100,
  offers: [
    {
      offer_id: 11,
      name: "First offer",
      share: 50,
      pinned: false,
      removed: false,
      state: "active",
    },
    {
      offer_id: 12,
      name: "Second offer",
      share: 50,
      pinned: false,
      removed: false,
      state: "active",
    },
  ],
};
const campaign = { id: 101, name: "test_skudarnov_example", streams: [stream] };

function button(text: string, target: ParentNode = host): HTMLButtonElement {
  const found = [...target.querySelectorAll("button")].find(
    (x) => x.textContent?.trim() === text,
  );
  if (!(found instanceof HTMLButtonElement))
    throw new Error(`Button not found: ${text}`);
  return found;
}

function respond(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function loginAndSelect() {
  const input = host.querySelector("input");
  if (!input) throw new Error("Missing login input");
  input.value = crypto.randomUUID() + crypto.randomUUID();
  input.dispatchEvent(new Event("input", { bubbles: true }));
  host
    .querySelector("form")
    ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await vi.waitFor(() => expect(host.textContent).toContain("Распределение"));
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem("workbench-campaign", "101");
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit) => {
      if (input.endsWith("/meta"))
        return respond({
          prefix: "test_skudarnov_",
          countries: [{ id: "AU", name: "Australia" }],
        });
      if (input.endsWith("/resources"))
        return respond({
          campaigns: [{ id: 101, name: campaign.name }],
          offers: [],
          domains: [],
          groups: [],
          sources: [],
        });
      if (init.method === "PATCH")
        return respond({
          ...stream,
          revision: 2,
          dirty: true,
          offers: [
            { ...stream.offers[0], removed: true, share: 0 },
            { ...stream.offers[1], share: 100 },
          ],
        });
      if (input.endsWith("/cancel")) return respond({ ...stream, revision: 3 });
      if (input.endsWith("/push")) return respond({ ...stream, revision: 3 });
      return respond(campaign);
    }),
  );
  host = document.createElement("div");
  document.body.appendChild(host);
  app = createApp(App);
  app.mount(host);
});

afterEach(() => {
  app.unmount();
  host.remove();
  vi.unstubAllGlobals();
});

describe("working editor", () => {
  it("refresh replaces an unsubmitted percentage with the saved value", async () => {
    await loginAndSelect();
    const input = host.querySelector<HTMLInputElement>(
      '[aria-label="Доля оффера 11"]',
    );
    if (!input) throw new Error("Missing share input");
    input.value = "25";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    button("Обновить локально").click();
    await vi.waitFor(() => expect(input.value).toBe("50"));
    expect(
      vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "PATCH"),
    ).toBe(false);
  });
  it("loads saved campaign state and submits a versioned local edit", async () => {
    await loginAndSelect();
    button("Убрать").click();
    await vi.waitFor(() =>
      expect(host.textContent).toContain("Локальный черновик"),
    );
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(call?.[0]).toBe("/api/v1/streams/502/offers");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      revision: 1,
      action: "remove",
      offer_id: 11,
    });
    expect(call?.[1]?.headers).toHaveProperty("Idempotency-Key");
    expect(host.textContent).toContain("Удалён из потока");
    expect(button("Push в Keitaro").disabled).toBe(false);
  });

  it("cancel sends the current revision and removes draft controls", async () => {
    await loginAndSelect();
    button("Убрать").click();
    await vi.waitFor(() =>
      expect(host.textContent).toContain("Локальный черновик"),
    );
    button("Cancel").click();
    await vi.waitFor(() =>
      expect(host.textContent).toContain("Локальные изменения отменены."),
    );
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([path]) => String(path).endsWith("/cancel"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ revision: 2 });
    expect(host.textContent).not.toContain("Push в Keitaro");
  });

  it("shows an API conflict without losing the visible draft", async () => {
    await loginAndSelect();
    button("Убрать").click();
    await vi.waitFor(() =>
      expect(host.textContent).toContain("Локальный черновик"),
    );
    vi.mocked(fetch).mockResolvedValueOnce(
      respond({ detail: "Офферы изменены в Keitaro." }, 409),
    );
    button("Push в Keitaro").click();
    await vi.waitFor(() =>
      expect(host.querySelector('[role="alert"]')?.textContent).toContain(
        "Офферы изменены",
      ),
    );
    expect(host.textContent).toContain("Локальный черновик");
    expect(host.textContent).toContain("Вернуть");
  });
});
