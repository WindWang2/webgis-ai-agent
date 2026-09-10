import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StoryMarkdown from "./story-markdown";

describe("StoryMarkdown (FRONT-02)", () => {
  it("allows safe HTTP and HTTPS links", () => {
    render(<StoryMarkdown text="[Safe Link](https://example.com/docs)" />);
    const link = screen.getByRole("link", { name: "Safe Link" });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "https://example.com/docs");
  });

  it("sanitizes dangerous javascript: and data: protocol links", () => {
    render(
      <StoryMarkdown
        text={`[Malicious JS](javascript:alert("xss")) and [Data Link](data:image/svg+xml;base64,PHN2Zz4=)`}
      />
    );
    const links = screen.queryAllByRole("link");
    for (const link of links) {
      const href = link.getAttribute("href");
      expect(href).not.toContain("javascript:");
      expect(href).not.toContain("data:");
    }
  });
});
