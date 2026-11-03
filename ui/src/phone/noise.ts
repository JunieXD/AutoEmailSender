// The phone's side of the encrypted session with the computer (docs/development.md#手机远程):
// Noise NNpsk0_25519_ChaChaPoly_SHA256, as the computer's `snow` speaks it. Written here rather than
// with WebCrypto, which in-app browsers such as WeChat's may lack X25519 for.
import { chacha20poly1305 } from "@noble/ciphers/chacha.js";
import { x25519 } from "@noble/curves/ed25519.js";
import { hmac } from "@noble/hashes/hmac.js";
import { sha256 } from "@noble/hashes/sha2.js";

const encoder = new TextEncoder();
const PROTOCOL = encoder.encode("Noise_NNpsk0_25519_ChaChaPoly_SHA256");
const TAG = 16;
/** What one Noise message carries of a JSON message, less the byte saying whether it is the last. */
const PART = 65535 - TAG - 1;
/** The longest JSON message either end sends: uploading a 20 MB material, in base64. */
const MAX_JSON = 32 * 1024 * 1024;

function concat(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

/** Noise's HKDF over HMAC-SHA256, with all three outputs; most steps use the first two. */
function hkdf(chainingKey: Uint8Array, input: Uint8Array): [Uint8Array, Uint8Array, Uint8Array] {
  const temp = hmac(sha256, chainingKey, input);
  const first = hmac(sha256, temp, Uint8Array.of(1));
  const second = hmac(sha256, temp, concat(first, Uint8Array.of(2)));
  return [first, second, hmac(sha256, temp, concat(second, Uint8Array.of(3)))];
}

export class CipherState {
  private n = 0n;
  constructor(private readonly key: Uint8Array) {}

  private nonce(): Uint8Array {
    const nonce = new Uint8Array(12);
    new DataView(nonce.buffer).setBigUint64(4, this.n++, true);
    return nonce;
  }

  encrypt(ad: Uint8Array, plain: Uint8Array): Uint8Array<ArrayBuffer> {
    return chacha20poly1305(this.key, this.nonce(), ad).encrypt(plain);
  }

  decrypt(ad: Uint8Array, sealed: Uint8Array): Uint8Array {
    return chacha20poly1305(this.key, this.nonce(), ad).decrypt(sealed);
  }
}

/** The phone's handshake, as the initiator. `ephemeral` is for tests; each connection makes one. */
export class Handshake {
  private hash: Uint8Array;
  private chainingKey: Uint8Array;
  private cipher: CipherState | null = null;
  private readonly secret: Uint8Array;

  constructor(
    prologue: Uint8Array,
    private readonly psk: Uint8Array,
    ephemeral = x25519.utils.randomSecretKey(),
  ) {
    // A protocol name longer than the hash is hashed.
    this.hash = sha256(PROTOCOL);
    this.chainingKey = this.hash;
    this.mixHash(prologue);
    this.secret = ephemeral;
  }

  /** The handshake hash, which both ends share once it is done. */
  get handshakeHash(): Uint8Array {
    return this.hash;
  }

  private mixHash(data: Uint8Array) {
    this.hash = sha256(concat(this.hash, data));
  }

  private mixKey(input: Uint8Array) {
    const [chainingKey, key] = hkdf(this.chainingKey, input);
    this.chainingKey = chainingKey;
    this.cipher = new CipherState(key);
  }

  private encryptAndHash(plain: Uint8Array): Uint8Array {
    const sealed = this.cipher ? this.cipher.encrypt(this.hash, plain) : plain;
    this.mixHash(sealed);
    return sealed;
  }

  private decryptAndHash(sealed: Uint8Array): Uint8Array {
    const plain = this.cipher ? this.cipher.decrypt(this.hash, sealed) : sealed;
    this.mixHash(sealed);
    return plain;
  }

  /** `-> psk, e`, with `payload`. */
  write(payload: Uint8Array = new Uint8Array()): Uint8Array {
    const [chainingKey, hash, key] = hkdf(this.chainingKey, this.psk);
    this.chainingKey = chainingKey;
    this.mixHash(hash);
    this.cipher = new CipherState(key);
    const ephemeral = x25519.getPublicKey(this.secret);
    this.mixHash(ephemeral);
    // With a pre-shared key, the ephemeral key is mixed into the encryption key too.
    this.mixKey(ephemeral);
    return concat(ephemeral, this.encryptAndHash(payload));
  }

  /** `<- e, ee`: the computer's answer, its payload and the two directions of the session. */
  read(message: Uint8Array): { payload: Uint8Array; send: CipherState; receive: CipherState } {
    const remote = message.subarray(0, 32);
    this.mixHash(remote);
    this.mixKey(remote);
    this.mixKey(x25519.getSharedSecret(this.secret, remote));
    const payload = this.decryptAndHash(message.subarray(32));
    const [send, receive] = hkdf(this.chainingKey, new Uint8Array());
    return { payload, send: new CipherState(send), receive: new CipherState(receive) };
  }
}

/** JSON messages in parts, each the first byte saying whether it is the last. */
export class Session {
  private partial: Uint8Array[] = [];
  private length = 0;

  constructor(
    private readonly sending: CipherState,
    private readonly receiving: CipherState,
  ) {}

  seal(json: string): Uint8Array<ArrayBuffer>[] {
    const bytes = encoder.encode(json);
    const count = Math.ceil(bytes.length / PART);
    const messages = [];
    for (let i = 0; i < count; i++) {
      const plain = concat(Uint8Array.of(i + 1 === count ? 1 : 0), bytes.subarray(i * PART, (i + 1) * PART));
      messages.push(this.sending.encrypt(new Uint8Array(), plain));
    }
    return messages;
  }

  /** The JSON message once its last part has come; throws on anything else. */
  open(message: Uint8Array): string | null {
    const plain = this.receiving.decrypt(new Uint8Array(), message);
    const [last] = plain;
    if (last === undefined || last > 1) throw new Error("malformed message");
    this.length += plain.length - 1;
    if (this.length > MAX_JSON) throw new Error("message too long");
    this.partial.push(plain.subarray(1));
    if (last === 0) return null;
    const json = new TextDecoder().decode(concat(...this.partial));
    this.partial = [];
    this.length = 0;
    return json;
  }
}
