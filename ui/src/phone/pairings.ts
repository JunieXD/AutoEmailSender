// The computers this phone paired with, kept by the browser. The loader at /m/ and each version's
// page under it share the origin, and so the storage.

export type Pairing = {
  room: string;
  device: string;
  /** The pairing key, in base64url. */
  key: string;
  /** The computer's name, once it has let the phone in. */
  computer: string | null;
};

const STORAGE = "aes-pairings";
const ID = /^[0-9a-f]{32}$/;
const KEY = /^[\w-]{43}$/;

/** The browser's record is not one this page can read, as one an older or newer page wrote. */
export class Unreadable extends Error {}

/** The pairings kept; throws `Unreadable` for a record that is not a list of them. */
export function pairings(): Pairing[] {
  const saved = localStorage.getItem(STORAGE);
  if (saved === null) return [];
  let list: unknown;
  try {
    list = JSON.parse(saved);
  } catch {
    throw new Unreadable();
  }
  if (!Array.isArray(list) || !list.every(isPairing)) throw new Unreadable();
  return list;
}

function isPairing(p: unknown): p is Pairing {
  return (
    typeof p === "object" &&
    p !== null &&
    "room" in p &&
    typeof p.room === "string" &&
    ID.test(p.room) &&
    "device" in p &&
    typeof p.device === "string" &&
    ID.test(p.device) &&
    "key" in p &&
    typeof p.key === "string" &&
    KEY.test(p.key) &&
    "computer" in p &&
    (p.computer === null || typeof p.computer === "string")
  );
}

/** Forgets every pairing, as when the record cannot be read. */
export function forgetAll() {
  localStorage.removeItem(STORAGE);
}

function store(list: Pairing[]) {
  localStorage.setItem(STORAGE, JSON.stringify(list));
}

/** The pairing in a scanned code's address, `#p=<room>.<device>.<key>`. */
export function scanned(hash: string): Pairing | null {
  const [, room, device, key] = /^#p=([0-9a-f]{32})\.([0-9a-f]{32})\.([\w-]{43})$/.exec(hash) ?? [];
  return room && device && key ? { room, device, key, computer: null } : null;
}

/** Keeps the pairing, in place of an earlier one with the same computer. */
export function save(pairing: Pairing) {
  store([pairing, ...pairings().filter((p) => p.room !== pairing.room)]);
}

export function forget(room: string) {
  store(pairings().filter((p) => p.room !== room));
}

export function named(room: string, computer: string) {
  store(pairings().map((p) => (p.room === room ? { ...p, computer } : p)));
}

/** The pairing key's bytes. */
export function keyBytes(pairing: Pairing): Uint8Array {
  const base64 = pairing.key.replaceAll("-", "+").replaceAll("_", "/");
  return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
}
