<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import {
  api,
  ApiError,
  type Campaign,
  type Meta,
  type Resources,
  type Stream,
} from "./api";

const token = ref(sessionStorage.getItem("workbench-token") ?? "");
const connected = ref(false);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const mode = ref<"editor" | "create">("editor");
const meta = ref<Meta>({ prefix: "", countries: [] });
const resources = ref<Resources>({
  campaigns: [],
  domains: [],
  groups: [],
  sources: [],
  offers: [],
});
const selected = ref(Number(localStorage.getItem("workbench-campaign") ?? 0));
const campaign = ref<Campaign | null>(null);
const showArchive = ref(true);
const search = ref("");
const addIds = reactive<Record<number, number>>({});
const pinValues = reactive<Record<string, number>>({});
const form = reactive({
  name: "",
  country: "",
  offer_id: 0,
  domain_id: 0,
  group_id: 0,
  traffic_source_id: 0,
});
const input =
  "min-h-10 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-100 disabled:bg-slate-100";
const button =
  "min-h-10 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-700 disabled:cursor-wait disabled:opacity-50";
const primary = `${button} border-sky-700! bg-sky-700! text-white hover:bg-sky-800!`;
const colors = ["bg-sky-700", "bg-teal-600", "bg-amber-500", "bg-slate-500"];
const campaigns = computed(() =>
  resources.value.campaigns.filter((x) =>
    `${x.id} ${x.name}`.toLowerCase().includes(search.value.toLowerCase()),
  ),
);
const dirtyCount = computed(
  () => campaign.value?.streams.filter((x) => x.dirty).length ?? 0,
);

async function run(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await action();
  } catch (caught: unknown) {
    const message =
      caught instanceof Error ? caught.message : "Неизвестная ошибка";
    console.error(
      "Workbench operation failed",
      caught instanceof ApiError ? caught.status : "network",
    );
    error.value = message;
  } finally {
    busy.value = false;
  }
}

function clearPinInputs() {
  for (const key of Object.keys(pinValues)) delete pinValues[key];
}

async function readLocal() {
  if (!selected.value) {
    campaign.value = null;
    return;
  }
  try {
    campaign.value = await api<Campaign>(
      token.value,
      `/campaigns/${selected.value}`,
    );
    clearPinInputs();
  } catch (caught: unknown) {
    if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
    console.info("Selected campaign has not been imported locally.");
    campaign.value = null;
  }
}

async function login() {
  await run(async () => {
    const [nextMeta, nextResources] = await Promise.all([
      api<Meta>(token.value, "/meta"),
      api<Resources>(token.value, "/resources"),
    ]);
    meta.value = nextMeta;
    resources.value = nextResources;
    connected.value = true;
    sessionStorage.setItem("workbench-token", token.value);
    await readLocal();
  });
}

function chooseCampaign(id: number) {
  selected.value = id;
  localStorage.setItem("workbench-campaign", String(id));
  void run(readLocal);
}

async function fetchRemote() {
  await run(async () => {
    campaign.value = await api<Campaign>(
      token.value,
      `/campaigns/${selected.value}/fetch`,
      "POST",
    );
    clearPinInputs();
    notice.value = "Состояние загружено из Keitaro.";
  });
}

function updateStream(stream: Stream) {
  if (campaign.value)
    campaign.value.streams = campaign.value.streams.map((x) =>
      x.id === stream.id ? stream : x,
    );
  for (const offer of stream.offers)
    delete pinValues[`${stream.id}:${offer.offer_id}`];
}

async function edit(stream: Stream, action: string, offerId: number) {
  await run(async () => {
    const body = {
      revision: stream.revision,
      action,
      offer_id: offerId,
      ...(action === "pin"
        ? {
            share:
              pinValues[`${stream.id}:${offerId}`] ??
              stream.offers.find((x) => x.offer_id === offerId)?.share,
          }
        : {}),
    };
    updateStream(
      await api<Stream>(
        token.value,
        `/streams/${stream.id}/offers`,
        "PATCH",
        body,
      ),
    );
    if (action === "add") addIds[stream.id] = 0;
    notice.value =
      "Черновик сохранён локально. Для отправки в Keitaro нажмите Push.";
  });
}

async function streamAction(stream: Stream, action: "push" | "cancel") {
  await run(async () => {
    updateStream(
      await api<Stream>(
        token.value,
        `/streams/${stream.id}/${action}`,
        "POST",
        { revision: stream.revision },
      ),
    );
    notice.value =
      action === "push"
        ? "Keitaro подтвердил состав офферов и их веса."
        : "Локальные изменения отменены.";
  });
}

async function create() {
  await run(async () => {
    const fingerprint = JSON.stringify(form);
    const stored: { fingerprint: string; key: string } | null = JSON.parse(
      localStorage.getItem("workbench-create") ?? "null",
    );
    const key =
      stored?.fingerprint === fingerprint ? stored.key : crypto.randomUUID();
    localStorage.setItem(
      "workbench-create",
      JSON.stringify({ fingerprint, key }),
    );
    const result = await api<Campaign>(
      token.value,
      "/campaigns",
      "POST",
      form,
      key,
    );
    campaign.value = result;
    selected.value = result.id;
    localStorage.setItem("workbench-campaign", String(result.id));
    resources.value.campaigns.unshift({ id: result.id, name: result.name });
    localStorage.removeItem("workbench-create");
    mode.value = "editor";
    notice.value = "Кампания и два потока созданы в Keitaro.";
  });
}

onMounted(() => {
  if (token.value) void login();
});
</script>

<template>
  <main
    v-if="!connected"
    class="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-12"
  >
    <p class="mb-3 font-mono text-xs uppercase tracking-widest text-sky-800">
      Keitaro Workbench
    </p>
    <h1 class="mb-3 text-3xl font-semibold tracking-tight">
      Управление кампаниями
    </h1>
    <p class="mb-8 text-sm leading-6 text-slate-600">
      Создавайте кампании и готовьте изменения офферов перед публикацией.
    </p>
    <form class="space-y-4" @submit.prevent="login">
      <label class="block text-sm font-semibold"
        >Ключ доступа
        <input
          v-model="token"
          :class="`${input} mt-2`"
          type="password"
          autocomplete="off"
          required
          aria-describedby="token-help"
        />
      </label>
      <p id="token-help" class="text-xs leading-5 text-slate-600">
        Значение APP_TOKEN из локального файла .env.
      </p>
      <button :disabled="busy" :class="`${primary} w-full`">
        {{ busy ? "Подключение..." : "Открыть рабочую область" }}
      </button>
    </form>
    <p
      v-if="error"
      role="alert"
      class="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800"
    >
      {{ error }}
    </p>
  </main>

  <div v-else class="min-h-screen">
    <header class="border-b border-slate-300 bg-white px-5 py-4 lg:px-8">
      <div
        class="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4"
      >
        <div>
          <p class="font-mono text-xs uppercase tracking-widest text-sky-800">
            Keitaro Workbench
          </p>
          <h1 class="mt-1 text-xl font-semibold tracking-tight">
            Кампании и распределение трафика
          </h1>
        </div>
        <nav aria-label="Разделы" class="flex gap-2">
          <button
            :class="mode === 'editor' ? primary : button"
            :disabled="busy"
            @click="mode = 'editor'"
          >
            Редактор
          </button>
          <button
            :class="mode === 'create' ? primary : button"
            :disabled="busy"
            @click="mode = 'create'"
          >
            Создать кампанию
          </button>
        </nav>
      </div>
    </header>

    <div class="mx-auto max-w-7xl px-5 py-6 lg:px-8">
      <p
        v-if="error"
        role="alert"
        class="mb-5 rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-900"
      >
        {{ error }}
      </p>
      <p
        v-if="notice"
        role="status"
        class="mb-5 border-l-4 border-sky-700 bg-sky-50 p-3 text-sm text-sky-950"
      >
        {{ notice }}
      </p>

      <form
        v-if="mode === 'create'"
        class="max-w-3xl rounded-lg border border-slate-300 bg-white p-6"
        @submit.prevent="create"
      >
        <h2 class="text-xl font-semibold">Новая кампания</h2>
        <p class="mt-2 text-sm leading-6 text-slate-600">
          Первый поток отправит выбранную страну на Google. Второй направит
          остальных посетителей на оффер.
        </p>
        <fieldset :disabled="busy" class="mt-6 grid gap-5 sm:grid-cols-2">
          <label class="text-sm font-semibold sm:col-span-2"
            >Название<input
              v-model="form.name"
              :class="`${input} mt-2`"
              required
              maxlength="180"
              :placeholder="`${meta.prefix}campaign1`"
            /><span class="mt-1 block text-xs font-normal text-slate-500"
              >Префикс {{ meta.prefix }} добавляется автоматически.</span
            ></label
          >
          <label class="text-sm font-semibold"
            >Страна<select
              v-model="form.country"
              :class="`${input} mt-2`"
              required
            >
              <option disabled value="">Выберите страну</option>
              <option v-for="x in meta.countries" :key="x.id" :value="x.id">
                {{ x.id }} - {{ x.name }}
              </option>
            </select></label
          >
          <label class="text-sm font-semibold"
            >Оффер<select
              v-model.number="form.offer_id"
              :class="`${input} mt-2`"
              required
            >
              <option disabled :value="0">Выберите оффер</option>
              <option v-for="x in resources.offers" :key="x.id" :value="x.id">
                #{{ x.id }} - {{ x.name }}
              </option>
            </select></label
          >
          <label class="text-sm font-semibold"
            >Домен<select
              v-model.number="form.domain_id"
              :class="`${input} mt-2`"
              required
            >
              <option disabled :value="0">Выберите домен</option>
              <option v-for="x in resources.domains" :key="x.id" :value="x.id">
                {{ x.name }}
              </option>
            </select></label
          >
          <label class="text-sm font-semibold"
            >Группа<select
              v-model.number="form.group_id"
              :class="`${input} mt-2`"
              required
            >
              <option disabled :value="0">Выберите группу</option>
              <option v-for="x in resources.groups" :key="x.id" :value="x.id">
                {{ x.name }}
              </option>
            </select></label
          >
          <label class="text-sm font-semibold"
            >Источник<select
              v-model.number="form.traffic_source_id"
              :class="`${input} mt-2`"
              required
            >
              <option disabled :value="0">Выберите источник</option>
              <option v-for="x in resources.sources" :key="x.id" :value="x.id">
                {{ x.name }}
              </option>
            </select></label
          >
        </fieldset>
        <div class="mt-7 flex items-center gap-4">
          <button :class="primary" :disabled="busy">
            {{ busy ? "Создание..." : "Создать в Keitaro" }}</button><span
            class="text-xs text-slate-500"
            >Два потока, одна кампания</span
          >
        </div>
      </form>

      <div v-else class="grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside>
          <label class="text-sm font-semibold"
            >Кампании<input
              v-model="search"
              :class="`${input} mt-2`"
              placeholder="Название или ID"
              type="search"
          /></label>
          <p class="my-3 text-xs leading-5 text-slate-500">
            Доступны кампании {{ meta.prefix }}*
          </p>
          <ul
            class="max-h-96 overflow-y-auto rounded-md border border-slate-300 bg-white"
          >
            <li
              v-for="x in campaigns"
              :key="x.id"
              class="border-b border-slate-200 last:border-0"
            >
              <button
                :disabled="busy"
                class="w-full px-3 py-3 text-left text-sm hover:bg-slate-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-sky-700 disabled:opacity-50"
                :class="
                  selected === x.id ? 'border-l-4 border-sky-700 bg-sky-50' : ''
                "
                @click="chooseCampaign(x.id)"
              >
                <span class="block break-words font-semibold">{{
                  x.name
                }}</span><span
                  class="mt-1 block font-mono text-xs text-slate-500"
                  >#{{ x.id }}</span
                >
              </button>
            </li>
            <li
              v-if="!campaigns.length"
              class="p-4 text-sm leading-6 text-slate-500"
            >
              Кампании не найдены. Создайте новую или измените поиск.
            </li>
          </ul>
        </aside>

        <section class="min-w-0" aria-label="Потоки кампании">
          <div
            v-if="selected"
            class="mb-5 flex flex-wrap items-start justify-between gap-4"
          >
            <div>
              <h2 class="break-all text-lg font-semibold">
                {{ campaign?.name ?? `Кампания #${selected}` }}
              </h2>
              <p class="mt-1 text-xs text-slate-600">
                {{
                  dirtyCount
                    ? `Черновиков: ${dirtyCount}`
                    : "Локальные изменения не ожидают публикации"
                }}
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button :class="button" :disabled="busy" @click="run(readLocal)">
                Обновить локально</button
              ><button
                :class="primary"
                :disabled="busy || dirtyCount > 0"
                @click="fetchRemote"
              >
                Fetch из Keitaro
              </button>
            </div>
          </div>
          <div
            v-if="!campaign"
            class="rounded-lg border border-dashed border-slate-400 bg-white px-6 py-16 text-center"
          >
            <h2 class="text-lg font-semibold">
              {{ selected ? "Загрузите потоки кампании" : "Выберите кампанию" }}
            </h2>
            <p class="mt-2 text-sm leading-6 text-slate-600">
              {{
                selected
                  ? "Нажмите Fetch из Keitaro, чтобы начать редактирование."
                  : "Откройте существующую кампанию слева или создайте новую."
              }}
            </p>
          </div>
          <template v-else>
            <label class="mb-4 flex items-center gap-2 text-xs text-slate-600"
              ><input
                v-model="showArchive"
                type="checkbox"
                class="size-4 accent-sky-700"
              />Показывать архив офферов</label
            >
            <article
              v-for="s in campaign.streams"
              :key="s.id"
              class="mb-5 overflow-hidden rounded-lg border border-slate-300 bg-white"
              :class="s.dirty ? 'border-l-4 border-l-amber-500' : ''"
            >
              <div
                class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4"
                :class="s.dirty ? 'bg-amber-50' : 'bg-slate-50'"
              >
                <div>
                  <h3 class="font-semibold">
                    {{ s.name }}
                    <span
                      class="ml-2 font-mono text-xs font-normal text-slate-500"
                      >#{{ s.id }}</span
                    >
                  </h3>
                  <p class="mt-1 text-xs text-slate-600">
                    {{
                      s.dirty ? "Локальный черновик" : "Сохранённое состояние"
                    }}
                    · версия {{ s.revision }}
                  </p>
                </div>
                <div v-if="s.dirty" class="flex gap-2">
                  <button
                    :class="button"
                    :disabled="busy"
                    @click="streamAction(s, 'cancel')"
                  >
                    Cancel</button
                  ><button
                    :class="primary"
                    :disabled="busy || s.total !== 100"
                    @click="streamAction(s, 'push')"
                  >
                    Push в Keitaro
                  </button>
                </div>
              </div>
              <p
                v-if="s.schema !== 'landings'"
                class="px-4 py-6 text-sm text-slate-600"
              >
                {{
                  s.schema === "deleted"
                    ? "Поток удалён в Keitaro."
                    : "Поток без редактируемых офферов. Его настройки сохраняются в Keitaro."
                }}
              </p>
              <template v-else>
                <div class="px-4 pt-4">
                  <div class="mb-2 flex justify-between text-xs">
                    <span class="text-slate-600"
                      >Распределение активного трафика</span
                    ><strong class="font-mono">{{ s.total }}%</strong>
                  </div>
                  <div
                    class="flex h-2 overflow-hidden rounded bg-slate-100"
                    aria-hidden="true"
                  >
                    <div
                      v-for="(o, index) in s.offers.filter(
                        (x) => !x.removed && x.state === 'active',
                      )"
                      :key="o.offer_id"
                      :class="colors[index % colors.length]"
                      :style="{ width: `${o.share}%` }"
                    ></div>
                  </div>
                </div>
                <div class="overflow-x-auto">
                  <table class="mt-3 w-full text-left text-sm">
                    <thead class="text-xs text-slate-500">
                      <tr>
                        <th class="px-4 py-3 font-normal">Оффер</th>
                        <th class="px-3 py-3 font-normal">Доля</th>
                        <th class="px-4 py-3 text-right font-normal">
                          Действия
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="o in s.offers.filter(
                          (x) => showArchive || !x.removed,
                        )"
                        :key="o.offer_id"
                        class="border-t border-slate-200"
                        :class="o.removed ? 'bg-slate-50 text-slate-500' : ''"
                      >
                        <td class="min-w-48 max-w-md px-4 py-3">
                          <span
                            class="mb-1 block font-mono text-xs text-slate-500"
                            >#{{ o.offer_id }}</span
                          ><span class="break-words">{{ o.name }}</span
                          ><span v-if="o.removed" class="mt-1 block text-xs"
                            >Удалён из потока</span
                          ><span
                            v-else-if="o.state === 'disabled'"
                            class="mt-1 block text-xs"
                            >Отключён в Keitaro</span
                          >
                        </td>
                        <td class="whitespace-nowrap px-3 py-3 font-mono">
                          <span v-if="o.removed || o.state !== 'active'"
                            >0%</span
                          ><template v-else
                            ><input
                              type="number"
                              min="0"
                              max="100"
                              step="1"
                              :value="
                                pinValues[`${s.id}:${o.offer_id}`] ?? o.share
                              "
                              :disabled="busy || o.pinned"
                              :aria-label="`Доля оффера ${o.offer_id}`"
                              class="min-h-10 w-16 rounded border border-slate-300 bg-white px-2 disabled:bg-slate-100"
                              @input="
                                pinValues[`${s.id}:${o.offer_id}`] = Number(
                                  ($event.target as HTMLInputElement).value,
                                )
                              "
                            />%<span
                              v-if="o.pinned"
                              class="mt-1 block font-sans text-xs text-sky-800"
                              >Закреплено</span
                            ><span
                              v-else
                              class="mt-1 block font-sans text-xs text-slate-500"
                              >Распределено: {{ o.share }}%</span
                            ></template
                          >
                        </td>
                        <td class="px-4 py-3">
                          <div class="flex justify-end gap-2">
                            <button
                              v-if="o.removed"
                              :class="button"
                              :disabled="busy"
                              @click="edit(s, 'restore', o.offer_id)"
                            >
                              Вернуть</button
                            ><template v-else
                              ><button
                                v-if="o.state === 'active'"
                                :class="button"
                                :disabled="busy"
                                @click="
                                  edit(
                                    s,
                                    o.pinned ? 'unpin' : 'pin',
                                    o.offer_id,
                                  )
                                "
                              >
                                {{
                                  o.pinned ? "Открепить" : "Закрепить"
                                }}</button
                              ><button
                                :class="`${button} text-red-800`"
                                :disabled="busy"
                                @click="edit(s, 'remove', o.offer_id)"
                              >
                                Убрать
                              </button></template
                            >
                          </div>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <form
                  class="flex flex-wrap gap-2 border-t border-slate-200 bg-slate-50 p-4"
                  @submit.prevent="edit(s, 'add', addIds[s.id] ?? 0)"
                >
                  <label class="min-w-48 flex-1"
                    ><span class="sr-only">Добавить оффер в {{ s.name }}</span
                    ><input
                      v-model.number="addIds[s.id]"
                      type="number"
                      min="1"
                      step="1"
                      :list="`offers-${s.id}`"
                      :class="input"
                      :disabled="busy"
                      required
                      placeholder="ID оффера"
                    /><datalist :id="`offers-${s.id}`">
                      <option
                        v-for="o in resources.offers"
                        :key="o.id"
                        :value="o.id"
                      >
                        {{ o.name }}
                      </option>
                    </datalist></label
                  ><button :class="button" :disabled="busy || !addIds[s.id]">
                    Добавить в черновик
                  </button>
                </form>
              </template>
            </article>
          </template>
        </section>
      </div>
    </div>
  </div>
</template>
