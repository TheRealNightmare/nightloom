import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { EntityType } from "../domain/entities";
import { sampleGraph } from "../demo/sampleGraph";
import { SearchBox } from "./SearchBox";

function renderSearch(hidden: EntityType[] = []) {
  const onPick = vi.fn();
  render(<SearchBox nodes={sampleGraph.nodes} hiddenTypes={new Set(hidden)} onPick={onPick} />);
  return { onPick, input: screen.getByRole("combobox", { name: "Search entities" }) };
}

describe("SearchBox", () => {
  it("is collapsed until something is typed", () => {
    const { input } = renderSearch();
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("lists matches and picks the first with Enter", async () => {
    const { input, onPick } = renderSearch();
    await userEvent.type(input, "mail");
    expect(input).toHaveAttribute("aria-expanded", "true");
    const options = screen.getAllByRole("option");
    expect(options[0]).toHaveTextContent("mail.example.org");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(input).toHaveAttribute("aria-activedescendant", options[0]?.id);

    await userEvent.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith(expect.stringContaining("mail"));
    expect(input).toHaveValue("");
  });

  it("moves through the options with the arrow keys (wrapping)", async () => {
    const { input, onPick } = renderSearch();
    await userEvent.type(input, "AS64");
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);

    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith("asn:64500");
  });

  it("picks an option with the mouse", () => {
    const { input, onPick } = renderSearch();
    fireEvent.change(input, { target: { value: "nginx" } });
    fireEvent.mouseDown(screen.getByRole("option", { name: /nginx/ }));
    expect(onPick).toHaveBeenCalledWith("tech:nginx");
  });

  it("says when nothing matches and ignores Enter and arrows", async () => {
    const { input, onPick } = renderSearch();
    await userEvent.type(input, "zzz-no-match");
    expect(screen.getByText("No matching entities")).toBeVisible();
    await userEvent.keyboard("{ArrowDown}{ArrowUp}{Enter}");
    expect(onPick).not.toHaveBeenCalled();
    expect(input).not.toHaveAttribute("aria-activedescendant");
  });

  it("does not offer entities of hidden types", async () => {
    const { input } = renderSearch(["subdomain"]);
    await userEvent.type(input, "www");
    expect(screen.queryByRole("option", { name: /^www\.example\.org/ })).toBeNull();
  });

  it("clears the query with Escape", async () => {
    const { input } = renderSearch();
    await userEvent.type(input, "vpn");
    await userEvent.keyboard("{Escape}");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });
});
