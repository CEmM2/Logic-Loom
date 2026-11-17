// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoverySearchBar } from "../components/assistant/discover/DiscoverySearchBar";
import { describeLibraryReason } from "../components/assistant/batches/BatchSourcesTable";
import { WatermarkToggle } from "../components/WatermarkToggle";
import { WATERMARK_STORAGE_KEY, getStoredWatermark } from "../hooks/useWatermark";
import { connectionAdvice } from "../routes/assistant/settings";

describe("connectionAdvice", () => {
  it("explains a Semantic Scholar 429 with the API-key option", () => {
    const text = connectionAdvice("semantic_scholar", "HTTP 429");
    expect(text).toMatch(/rate-limiting/);
    expect(text).toMatch(/API key/);
  });

  it("returns null when there is nothing specific to say", () => {
    expect(connectionAdvice("openalex", "something odd")).toBeNull();
  });
});

describe("describeLibraryReason", () => {
  it("turns bbt-path-unset into a next step", () => {
    const text = describeLibraryReason("bbt-path-unset", null);
    expect(text).toMatch(/Zotero is not connected/);
    expect(text).toMatch(/zsum ▸ Settings/);
    expect(text).not.toMatch(/bbt-path-unset/);
  });

  it("names the missing export path", () => {
    expect(describeLibraryReason("bbt-file-missing", "/x/lib.json")).toMatch(/\/x\/lib\.json/);
  });
});

describe("DiscoverySearchBar", () => {
  it("submits on Enter, trims, and explains the disabled button", () => {
    const onSubmit = vi.fn();
    render(<DiscoverySearchBar onSubmit={onSubmit} />);
    const button = screen.getByRole("button", { name: "Search" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("title")).toMatch(/search term/);

    const box = screen.getByLabelText("Discovery query");
    expect(box.tagName).toBe("TEXTAREA");
    expect(box.getAttribute("maxlength")).toBe("500");
    fireEvent.change(box, { target: { value: "  graphene  " } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("graphene");
  });
});

describe("WatermarkToggle", () => {
  afterEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.watermark;
  });

  it("defaults to dim and cycles dim → off → full, persisting the choice", () => {
    expect(getStoredWatermark()).toBe("dim");
    render(<WatermarkToggle />);
    const btn = screen.getByTestId("watermark-toggle");
    fireEvent.click(btn);
    expect(document.documentElement.dataset.watermark).toBe("off");
    expect(window.localStorage.getItem(WATERMARK_STORAGE_KEY)).toBe("off");
    fireEvent.click(btn);
    expect(document.documentElement.dataset.watermark).toBe("full");
  });
});
