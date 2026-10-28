import { describe, expect, it } from "vitest";

import ar from "../../messages/ar.json";
import en from "../../messages/en.json";

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  return Object.entries(tree).reduce<Record<string, string>>((out, [key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof value === "string" ? { ...out, [path]: value } : { ...out, ...flatten(value, path) };
  }, {});
}

/** Top-level ICU arguments ({name}, {count, plural, ...}) used by a message. */
function argumentsOf(message: string): string[] {
  const names = new Set<string>();
  let depth = 0;
  for (let i = 0; i < message.length; i += 1) {
    if (message[i] === "{") {
      if (depth === 0) {
        const match = /^\{\s*([a-zA-Z]+)/.exec(message.slice(i));
        if (match?.[1]) names.add(match[1]);
      }
      depth += 1;
    } else if (message[i] === "}") depth -= 1;
  }
  return [...names].sort();
}

const EN = flatten(en as Tree);
const AR = flatten(ar as Tree);

describe("messages", () => {
  it("English and Arabic have the same keys", () => {
    expect(Object.keys(AR).sort()).toEqual(Object.keys(EN).sort());
  });

  it("every Arabic message uses the same arguments as English", () => {
    const mismatched = Object.keys(EN).filter(
      (key) => JSON.stringify(argumentsOf(EN[key]!)) !== JSON.stringify(argumentsOf(AR[key] ?? "")),
    );
    expect(mismatched).toEqual([]);
  });

  it("no message is empty", () => {
    expect(Object.entries({ ...EN, ...AR }).filter(([, v]) => !v.trim())).toEqual([]);
  });
});
