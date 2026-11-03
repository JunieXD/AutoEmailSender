// The phone's connection to the computer through the relay (docs/development.md#手机远程): the
// handshake, then requests and the computer's events as JSON.
import type { CallError } from "@/api/types";
import { Handshake, Session } from "./noise";
import { keyBytes, type Pairing } from "./pairings";

/** Close codes from the relay and the computer (crates/cloud-api, relay::close). */
export const CLOSE = { BROKEN: 4400, UNPAIRED: 4401, DENIED: 4403, OFFLINE: 4404, FULL: 4429 } as const;
/** What the browser reports for a connection that dropped, and what this page says for one gone quiet. */
const LOST = 1006;
/** How long the handshake's answer, the welcome to a paired phone, or the answer to a ping may take. */
const PROMPT = 10_000;
/**
 * How long a phone pairing for the first time may wait for its welcome: the five minutes the user has
 * to answer (crates/api, phones::PAIRING_FOR), and the time the answer takes to come.
 */
const ANSWER = 5 * 60_000 + PROMPT;

export type Welcome = { computer: string; version: string };

export type Status =
  | { kind: "ready"; welcome: Welcome }
  /** `code` is the relay's or the computer's, or what the browser saw when the network dropped. */
  | { kind: "closed"; code: number };

type Message =
  | { welcome: Welcome }
  | { pong: true }
  | { id: number; output: unknown }
  | { id: number; error: CallError }
  | { event: string; payload: unknown };

/** A message from the computer, and the session it came in. */
type Read = { session: Session; message: Message };

const encoder = new TextEncoder();

/** What a call that cannot reach the computer fails with. */
export const DISCONNECTED: CallError = { code: "external", message: "和电脑的连接断开了，正在重连…" };

export class Connection {
  private readonly socket: WebSocket;
  /** Before the handshake's answer; then saying hello until the welcome. */
  private stage: { kind: "handshake" } | { kind: "hello" | "ready"; session: Session } = { kind: "handshake" };
  private nextId = 0;
  private readonly calls = new Map<number, { resolve: (output: unknown) => void; reject: (e: CallError) => void }>();
  private ended = false;
  /** Set while the computer is to answer: the handshake, the hello, or a ping. */
  private deadline: ReturnType<typeof setTimeout> | undefined;
  private readonly welcomeWithin: number;

  constructor(
    pairing: Pairing,
    private readonly onStatus: (status: Status) => void,
    private readonly onEvent: (event: string, payload: unknown) => void,
  ) {
    this.welcomeWithin = pairing.computer === null ? ANSWER : PROMPT;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(`${scheme}://${location.host}/v1/relay/${pairing.room}/phone`);
    this.socket.binaryType = "arraybuffer";
    // The handshake is bound to this computer and this phone.
    const handshake = new Handshake(
      encoder.encode(`auto-email-sender remote 1${pairing.room}${pairing.device}`),
      keyBytes(pairing),
    );
    this.socket.addEventListener("open", () => {
      const first = handshake.write();
      const device = encoder.encode(pairing.device);
      const message = new Uint8Array(device.length + first.length);
      message.set(device);
      message.set(first, device.length);
      this.socket.send(message);
    });
    // The relay sends the phone binary frames only.
    this.socket.addEventListener("message", ({ data }: MessageEvent<ArrayBuffer>) => {
      if (this.ended) return;
      clearTimeout(this.deadline);
      this.deadline = undefined;
      let read: Read | null;
      try {
        read = this.read(handshake, new Uint8Array(data));
      } catch (error) {
        console.error("phone session", error);
        this.socket.close(CLOSE.BROKEN);
        this.end(CLOSE.BROKEN);
        return;
      }
      // Outside the catch: what the page does with a message is not the computer's error.
      if (read) this.handle(read);
    });
    this.socket.addEventListener("close", ({ code }) => this.end(code));
    this.expect(PROMPT);
  }

  /**
   * Over at once, rather than when the browser says so, which for a connection lost with the
   * network can take minutes: the calls waiting fail, and the status says it once.
   */
  private end(code: number) {
    if (this.ended) return;
    this.ended = true;
    clearTimeout(this.deadline);
    this.socket.close();
    this.stage = { kind: "handshake" };
    for (const call of this.calls.values()) call.reject(DISCONNECTED);
    this.calls.clear();
    this.onStatus({ kind: "closed", code });
  }

  private expect(within: number) {
    this.deadline = setTimeout(() => this.end(LOST), within);
  }

  /** Makes sure the computer still answers, as after the page was in the background. */
  check() {
    if (this.stage.kind !== "ready" || this.deadline !== undefined) return;
    this.send(this.stage.session, { ping: true });
    this.expect(PROMPT);
  }

  /** The message in a frame from the computer, once it has all of one; throws for a broken one. */
  private read(handshake: Handshake, data: Uint8Array): Read | null {
    if (this.stage.kind === "handshake") {
      const { send, receive } = handshake.read(data);
      this.stage = { kind: "hello", session: new Session(send, receive) };
      this.send(this.stage.session, { hello: { userAgent: navigator.userAgent } });
      this.expect(this.welcomeWithin);
      return null;
    }
    const { session } = this.stage;
    const json = session.open(data);
    // The computer's app, authenticated by the handshake, speaks this protocol.
    return json === null ? null : { session, message: JSON.parse(json) };
  }

  private handle({ session, message }: Read) {
    if ("welcome" in message) {
      this.stage = { kind: "ready", session };
      this.onStatus({ kind: "ready", welcome: message.welcome });
    } else if ("pong" in message) {
      // Hearing it was the point.
    } else if ("event" in message) {
      this.onEvent(message.event, message.payload);
    } else {
      const call = this.calls.get(message.id);
      this.calls.delete(message.id);
      if ("output" in message) call?.resolve(message.output);
      else call?.reject(message.error);
    }
  }

  private send(session: Session, message: unknown) {
    for (const part of session.seal(JSON.stringify(message))) this.socket.send(part);
  }

  /** Runs an operation on the computer; rejects with a `CallError`. */
  call(op: string, input: unknown): Promise<unknown> {
    const stage = this.stage;
    // Closing, the socket would drop what it is given.
    if (stage.kind !== "ready" || this.socket.readyState !== WebSocket.OPEN) return Promise.reject(DISCONNECTED);
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.calls.set(id, { resolve, reject });
      this.send(stage.session, { id, op, input });
    });
  }
}
