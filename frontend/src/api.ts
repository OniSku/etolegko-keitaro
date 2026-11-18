export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  token: string,
  path: string,
  method = "GET",
  body?: unknown,
  key?: string,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET"
        ? { "Idempotency-Key": key ?? crypto.randomUUID() }
        : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(
      response.status,
      data.detail ?? `HTTP ${response.status}`,
    );
  return data as T;
}

export interface Choice {
  id: number;
  name: string;
}
export interface Offer {
  offer_id: number;
  name: string;
  share: number;
  pinned: boolean;
  removed: boolean;
  state: string;
}
export interface Stream {
  id: number;
  name: string;
  schema: string;
  revision: number;
  dirty: boolean;
  total: number;
  offers: Offer[];
}
export interface Campaign {
  id: number;
  name: string;
  streams: Stream[];
}
export interface Resources {
  campaigns: Choice[];
  domains: Choice[];
  groups: Choice[];
  sources: Choice[];
  offers: Choice[];
}
export interface Meta {
  prefix: string;
  countries: { id: string; name: string }[];
}
