"use strict";

const assert = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs
  .readFileSync("static/app.js", "utf8")
  .split('$("#quoteForm").addEventListener("input"')[0];
const context = {
  Intl,
  URLSearchParams,
  window: {
    location: { search: "" },
    LodestarLocationResolver: { attach: () => ({ resolveAll: async () => true }) }
  }
};

vm.createContext(context);
vm.runInContext(source, context);

function estimate(values) {
  return context.competitiveRateEstimate(values.map((priceUsd) => ({ price_usd: priceUsd })));
}

assert.equal(estimate([]), null);
assert.deepEqual({ ...estimate([100]) }, { average: 100, sampleCount: 1, validQuoteCount: 1 });
assert.deepEqual({ ...estimate([200, 100]) }, { average: 150, sampleCount: 2, validQuoteCount: 2 });

for (const [count, sampleCount, average] of [
  [5, 3, 2],
  [10, 4, 2.5],
  [15, 5, 3],
  [30, 10, 5.5]
]) {
  const values = Array.from({ length: count }, (_, index) => count - index);
  const result = estimate(values);
  assert.equal(result.sampleCount, sampleCount);
  assert.equal(result.validQuoteCount, count);
  assert.equal(result.average, average);
}

assert.deepEqual(
  { ...estimate(["$300.00", "invalid", "100", null, "200"]) },
  { average: 200, sampleCount: 3, validQuoteCount: 3 }
);

console.log("competitiveRateEstimate targeted cases passed");
