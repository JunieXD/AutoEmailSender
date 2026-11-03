// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { pairings, save, Unreadable } from "./pairings";

test("a record of pairings the page cannot read is said so, not taken as pairings", () => {
  for (const saved of ["null", "{", JSON.stringify([{ room: "0".repeat(32) }])]) {
    localStorage.setItem("aes-pairings", saved);
    expect(() => pairings()).toThrow(Unreadable);
  }
  localStorage.removeItem("aes-pairings");
  const pairing = { room: "0".repeat(32), device: "1".repeat(32), key: "k".repeat(43), computer: null };
  save(pairing);
  expect(pairings()).toEqual([pairing]);
});
