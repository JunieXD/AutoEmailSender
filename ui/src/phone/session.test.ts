// @vitest-environment happy-dom
import { bytesToHex, hexToBytes } from "@noble/hashes/utils.js";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DISCONNECTED, Connection, type Status } from "./session";
import transcript from "./snow-transcript.json";

/** The relay's end of the phone's socket, as far as the page uses it. */
class Socket extends EventTarget {
  static readonly OPEN = 1;
  static last: Socket;
  readyState = 0;
  binaryType = "blob";
  readonly sent: Uint8Array[] = [];

  constructor() {
    super();
    Socket.last = this;
  }

  send(data: Uint8Array) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = Socket.OPEN;
    this.dispatchEvent(new Event("open"));
  }

  receive(hex: string) {
    this.dispatchEvent(new MessageEvent("message", { data: hexToBytes(hex).buffer }));
  }
}

// The ephemeral key the transcript was made with.
vi.mock("@noble/curves/ed25519.js", async (original) => {
  const curves = await original<typeof import("@noble/curves/ed25519.js")>();
  const { initiatorEphemeral } = (await import("./snow-transcript.json")).default;
  const utils = { ...curves.x25519.utils, randomSecretKey: () => hexToBytes(initiatorEphemeral) };
  return { ...curves, x25519: { ...curves.x25519, utils } };
});

const key = btoa(String.fromCharCode(...hexToBytes(transcript.key)))
  .replaceAll("+", "-")
  .replaceAll("/", "_")
  .replace(/=+$/, "");

/** A connection to the transcript's computer, with what it reported. */
function connect(computer: string | null) {
  const statuses: Status[] = [];
  const connection = new Connection(
    { room: transcript.room, device: transcript.device, key, computer },
    (status) => statuses.push(status),
    () => {},
  );
  return { connection, socket: Socket.last, statuses };
}

/** One that got through the handshake. */
function shaken(computer: string | null) {
  const { connection, socket, statuses } = connect(computer);
  socket.open();
  expect(socket.sent.map(bytesToHex)).toEqual([transcript.first]);
  socket.receive(transcript.answer);
  // The hello.
  expect(socket.sent).toHaveLength(2);
  return { connection, socket, statuses };
}

const lost = { kind: "closed", code: 1006 };

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", Socket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("a computer that stops answering is given up on, whatever the phone waits for", async () => {
  // The handshake's answer.
  const connecting = connect(null);
  vi.advanceTimersByTime(10_000);
  expect(connecting.statuses).toEqual([lost]);

  // The welcome of a paired phone.
  const paired = shaken("MacBook Pro");
  vi.advanceTimersByTime(10_000);
  expect(paired.statuses).toEqual([lost]);

  // The welcome of a phone pairing, which waits for the user to answer on the computer.
  const pairing = shaken(null);
  vi.advanceTimersByTime(5 * 60_000);
  expect(pairing.statuses).toEqual([]);
  vi.advanceTimersByTime(10_000);
  expect(pairing.statuses).toEqual([lost]);

  // The answer to a ping, once in.
  const ready = shaken("MacBook Pro");
  const [welcome] = transcript.fromComputer;
  for (const part of welcome?.parts ?? []) ready.socket.receive(part);
  expect(ready.statuses).toEqual([{ kind: "ready", welcome: { computer: "MacBook Pro" } }]);
  const call = ready.connection.call("getProfile", {});
  ready.connection.check();
  expect(ready.socket.sent).toHaveLength(4);
  vi.advanceTimersByTime(10_000);
  expect(ready.statuses.at(-1)).toEqual(lost);
  await expect(call).rejects.toEqual(DISCONNECTED);
});
