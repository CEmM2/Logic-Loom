import { describe, expect, it } from "vitest";

import { parseSseChunk } from "../api/graph";
import { describeProviderError } from "../routes/assistant/discover";

describe("parseSseChunk", () => {
  it("splits complete events and keeps the partial tail", () => {
    const { messages, rest } = parseSseChunk(
      'event: progress\ndata: {"step":1}\n\n: comment\ndata: a\ndata: b\n\nevent: done\ndata: x',
    );
    expect(messages).toEqual([
      { event: "progress", data: '{"step":1}' },
      { event: "message", data: "a\nb" },
    ]);
    expect(rest).toBe("event: done\ndata: x");
  });

  it("normalises CRLF line endings", () => {
    const { messages, rest } = parseSseChunk("event: done\r\ndata: ok\r\n\r\n");
    expect(messages).toEqual([{ event: "done", data: "ok" }]);
    expect(rest).toBe("");
  });
});

describe("describeProviderError", () => {
  it("explains a Semantic Scholar rate limit with the retry hint and key advice", () => {
    const text = describeProviderError({
      provider: "semantic_scholar",
      error: "provider_rate_limited",
      message: "429",
      retry_after: 30,
    });
    expect(text).toContain("Semantic Scholar is rate-limiting");
    expect(text).toContain("about 30s");
    expect(text).toContain("API key");
  });

  it("names the provider and message for other failures", () => {
    const text = describeProviderError({
      provider: "openalex",
      error: "provider_error",
      message: "timeout",
      retry_after: null,
    });
    expect(text).toContain("OpenAlex did not answer (timeout)");
  });
});
