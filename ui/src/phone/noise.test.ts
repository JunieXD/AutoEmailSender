import { hexToBytes, bytesToHex } from "@noble/hashes/utils.js";
import { describe, expect, test } from "vitest";
import cacophony from "./cacophony-nnpsk0.json";
import transcript from "./snow-transcript.json";
import { CipherState, Handshake, Session } from "./noise";

const empty = new Uint8Array();
const hex = (s: string) => hexToBytes(s);

describe("the phone's Noise session", () => {
  test("follows the official NNpsk0 test vector", () => {
    const [first, second, ...transport] = cacophony.messages;
    if (!first || !second) throw new Error("the vector has a handshake");
    const handshake = new Handshake(
      hex(cacophony.init_prologue),
      hex(cacophony.init_psks[0] ?? ""),
      hex(cacophony.init_ephemeral),
    );
    expect(bytesToHex(handshake.write(hex(first.payload)))).toBe(first.ciphertext);
    const { payload, send, receive } = handshake.read(hex(second.ciphertext));
    expect(bytesToHex(payload)).toBe(second.payload);
    expect(bytesToHex(handshake.handshakeHash)).toBe(cacophony.handshake_hash);
    transport.forEach((message, i) => {
      if (i % 2 === 0) expect(bytesToHex(send.encrypt(empty, hex(message.payload)))).toBe(message.ciphertext);
      else expect(bytesToHex(receive.decrypt(empty, hex(message.ciphertext)))).toBe(message.payload);
    });
  });

  test("talks with the computer's library", () => {
    const prologue = new TextEncoder().encode(`auto-email-sender remote 1${transcript.room}${transcript.device}`);
    const handshake = new Handshake(prologue, hex(transcript.key), hex(transcript.initiatorEphemeral));
    const device = new TextEncoder().encode(transcript.device);
    expect(bytesToHex(device) + bytesToHex(handshake.write())).toBe(transcript.first);
    const { payload, send, receive } = handshake.read(hex(transcript.answer));
    expect(payload).toEqual(empty);
    expect(bytesToHex(handshake.handshakeHash)).toBe(transcript.handshakeHash);
    const session = new Session(send, receive);
    for (const { json, parts } of transcript.fromComputer) {
      expect(parts.map((part) => session.open(hex(part)))).toEqual([json]);
    }
    for (const { json, parts } of transcript.fromPhone) {
      expect(session.seal(json).map(bytesToHex)).toEqual(parts);
    }
  });

  test("sends a long message in parts and reads it back whole", () => {
    const key = new Uint8Array(32).fill(9);
    const session = new Session(new CipherState(key), new CipherState(key));
    const json = JSON.stringify({ text: "长".repeat(30_000) });
    const messages = session.seal(json);
    expect(messages).toHaveLength(2);
    expect(messages.map((m) => session.open(m))).toEqual([null, json]);
  });
});
