// Day 4 of model UX — ModelsPage edit-affordance tests.
//
// Cases per the audit ask:
//   - dropdown renders models from available_models[brain]
//   - selection dispatches POST /api/v1/models/set
//   - optimistic update on success (UI reflects new value before
//     the refetch returns)
//   - revert + toast on 400 (validator-error response from server)
//   - refresh button calls discovery refresh
//   - brain switcher modal renders with preview-mode validator
//     findings
//   - comment-preservation modal gates save when has_comments is
//     true
//
// Mock api methods directly via vi.spyOn rather than fetch — the
// thin client in lib/api wraps fetch and gives a clean injection
// surface.

import { describe, expect, it, vi, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import { ModelsPage } from "./ModelsPage";
import { ApiError } from "../lib/api";
import type {
  ModelsState,
  ModelSetResponse,
  ModelBrainResponse,
  ModelDiscoveryRefreshResponse,
} from "../lib/types";
import * as apiMod from "../lib/api";

const TOKEN = "test-token";

// ──────────────────────────────────────────────────────────────────
// Fixture — model_ux_enabled: true so edit affordances render
// ──────────────────────────────────────────────────────────────────

function buildFixture(overrides: Partial<ModelsState> = {}): ModelsState {
  return {
    brain_kind: "claude-code",
    subsystems: [
      {
        name: "curator",
        configured: null,
        resolved_tier: "small",
        resolved_model_id: "haiku",
        findings: [],
      },
      {
        name: "goal_judge",
        configured: "small",
        resolved_tier: "small",
        resolved_model_id: "haiku",
        findings: [],
      },
    ],
    tier_overrides: {
      tiny: { configured: null, default: "haiku" },
      small: { configured: null, default: "haiku" },
      medium: { configured: null, default: "sonnet" },
      large: { configured: null, default: "sonnet" },
    },
    brain_inventory: ["claude-code", "null", "opencode"],
    global_findings: [],
    available_models: {
      "claude-code": ["haiku", "sonnet", "opus", "claude-haiku-4-5"],
      opencode: ["anthropic/claude-haiku-3-5", "anthropic/claude-sonnet-4"],
      null: [],
    },
    has_comments: false,
    model_ux_enabled: true,
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderWithFixture(state: ModelsState) {
  const onAuthFail = vi.fn();
  const apiSpy = vi.spyOn(apiMod.api, "models").mockResolvedValue(state);
  render(<ModelsPage token={TOKEN} onAuthFail={onAuthFail} />);
  await waitFor(() => expect(apiSpy).toHaveBeenCalled());
  await screen.findByText("Subsystem resolution");
  return { onAuthFail, apiSpy };
}

// ──────────────────────────────────────────────────────────────────
// Dropdown rendering + selection → POST
// ──────────────────────────────────────────────────────────────────

describe("ModelsPage edit affordances", () => {
  it("renders editable dropdowns when model_ux is enabled", async () => {
    await renderWithFixture(buildFixture());
    // Each subsystem row has a select with the appropriate aria-label.
    expect(screen.getByLabelText("Set curator")).toBeInTheDocument();
    expect(screen.getByLabelText("Set goal_judge")).toBeInTheDocument();
  });

  it("hides edit affordances when model_ux is disabled", async () => {
    await renderWithFixture(buildFixture({ model_ux_enabled: false }));
    expect(screen.queryByLabelText("Set curator")).not.toBeInTheDocument();
    expect(screen.getByText(/Edit affordances are off/i)).toBeInTheDocument();
  });

  it("dropdown options include abstract tiers + discovery list + current configured", async () => {
    await renderWithFixture(buildFixture());
    const select = screen.getByLabelText("Set curator") as HTMLSelectElement;
    const optionTexts = Array.from(select.options).map((o) => o.textContent);
    // Abstract tiers always present.
    expect(optionTexts).toContain("tiny");
    expect(optionTexts).toContain("small");
    expect(optionTexts).toContain("medium");
    expect(optionTexts).toContain("large");
    // Discovery list contributions.
    expect(optionTexts).toContain("claude-haiku-4-5");
    expect(optionTexts).toContain("opus");
  });

  it("selecting a value POSTs to /api/v1/models/set", async () => {
    const setSpy = vi
      .spyOn(apiMod.api, "setModel")
      .mockResolvedValue({
        ok: true,
        subsystem: "curator",
        value: "large",
        resolved_tier: "large",
        resolved_model_id: "sonnet",
        backup_path: null,
      } satisfies ModelSetResponse);
    await renderWithFixture(buildFixture());
    const select = screen.getByLabelText("Set curator") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "large" } });
    await waitFor(() => expect(setSpy).toHaveBeenCalledWith(
      TOKEN, { subsystem: "curator", value: "large" },
    ));
  });

  // ────────────────────────────────────────────────────────────────
  // Optimistic update: UI shows new value before POST returns
  // ────────────────────────────────────────────────────────────────

  it("optimistically updates the dropdown before POST resolves", async () => {
    // Use a pending promise that we control so we can inspect the
    // DOM between optimistic update and POST resolution.
    let resolveSet: (v: ModelSetResponse) => void;
    const setPromise = new Promise<ModelSetResponse>((res) => {
      resolveSet = res;
    });
    vi.spyOn(apiMod.api, "setModel").mockReturnValue(setPromise);
    await renderWithFixture(buildFixture());

    const select = screen.getByLabelText("Set curator") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "large" } });

    // Optimistic update lands synchronously: the dropdown's
    // current value is now "large" even though the POST hasn't
    // returned yet.
    await waitFor(() => expect(select.value).toBe("large"));

    // Resolve and clean up.
    resolveSet!({
      ok: true, subsystem: "curator", value: "large",
      resolved_tier: "large", resolved_model_id: "sonnet",
      backup_path: null,
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Revert + toast on validator-error
  // ────────────────────────────────────────────────────────────────

  it("reverts the dropdown and shows a toast when POST returns 400", async () => {
    vi.spyOn(apiMod.api, "setModel").mockRejectedValue(
      new ApiError(
        400,
        "[goal_judge] resolves to bare alias 'sonnet' on opencode -- fix: Use /model set goal_judge large",
      ),
    );
    // Refetch after the failed POST returns the original (un-edited)
    // state — that's what causes the dropdown to revert.
    const original = buildFixture();
    const apiSpy = vi.spyOn(apiMod.api, "models").mockResolvedValue(original);
    render(<ModelsPage token={TOKEN} onAuthFail={vi.fn()} />);
    await waitFor(() => expect(apiSpy).toHaveBeenCalled());
    await screen.findByText("Subsystem resolution");

    const select = screen.getByLabelText("Set curator") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "large" } });

    // Toast surfaces the error message verbatim (user sees the
    // validator's suggested_fix copy).
    await waitFor(() =>
      expect(
        screen.getByText(/goal_judge.*bare alias.*Use \/model set/),
      ).toBeInTheDocument(),
    );
    // Dropdown reverts to the canonical server state (the original
    // refetched state after the failed POST). Selectable.value
    // resets to the empty "default" option since curator was null.
    await waitFor(() => expect(select.value).toBe(""));
  });

  // ────────────────────────────────────────────────────────────────
  // Refresh button
  // ────────────────────────────────────────────────────────────────

  it("refresh button calls /api/v1/models/discovery/refresh", async () => {
    const refreshSpy = vi
      .spyOn(apiMod.api, "refreshModelDiscovery")
      .mockResolvedValue({
        ok: true,
        available_models: {
          "claude-code": ["haiku", "sonnet"],
          opencode: ["anthropic/x"],
          null: [],
        },
      } satisfies ModelDiscoveryRefreshResponse);
    await renderWithFixture(buildFixture());
    const btn = screen.getByLabelText("refresh model discovery");
    fireEvent.click(btn);
    await waitFor(() => expect(refreshSpy).toHaveBeenCalledWith(TOKEN));
    // Toast confirms the refresh.
    await waitFor(() =>
      expect(screen.getByText(/Discovery refreshed/)).toBeInTheDocument(),
    );
  });

  // ────────────────────────────────────────────────────────────────
  // Brain switcher modal
  // ────────────────────────────────────────────────────────────────

  it("brain switch button opens a confirm modal", async () => {
    await renderWithFixture(buildFixture());
    // Two switch-to buttons (opencode, null) since current is claude-code.
    const switchBtn = screen.getByText("opencode");
    fireEvent.click(switchBtn);
    expect(
      screen.getByRole("dialog", { name: /brain switch confirmation/i }),
    ).toBeInTheDocument();
    // Restart-required note in the modal.
    expect(screen.getByText(/Restart required/i)).toBeInTheDocument();
  });

  it("brain switcher modal warns about legacy-keys → opencode trap", async () => {
    await renderWithFixture(buildFixture());
    fireEvent.click(screen.getByText("opencode"));
    expect(
      screen.getByText(/legacy raw-string keys/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/docs\/migration\.md/i),
    ).toBeInTheDocument();
  });

  it("brain switcher modal Cancel keeps state as-is", async () => {
    const setBrainSpy = vi.spyOn(apiMod.api, "setBrain");
    await renderWithFixture(buildFixture());
    fireEvent.click(screen.getByText("opencode"));
    fireEvent.click(screen.getByText("Cancel"));
    // Modal closed; no POST.
    expect(
      screen.queryByRole("dialog", { name: /brain switch confirmation/i }),
    ).not.toBeInTheDocument();
    expect(setBrainSpy).not.toHaveBeenCalled();
  });

  it("brain switcher Confirm dispatches POST /api/v1/models/brain", async () => {
    const setBrainSpy = vi
      .spyOn(apiMod.api, "setBrain")
      .mockResolvedValue({
        ok: true,
        kind: "opencode",
        restart_required: true,
        warnings: [],
        backup_path: null,
      } satisfies ModelBrainResponse);
    await renderWithFixture(buildFixture());
    fireEvent.click(screen.getByText("opencode"));
    fireEvent.click(screen.getByText("Confirm switch"));
    await waitFor(() =>
      expect(setBrainSpy).toHaveBeenCalledWith(
        TOKEN, { kind: "opencode" },
      ),
    );
  });

  // ────────────────────────────────────────────────────────────────
  // Comment-preservation modal
  // ────────────────────────────────────────────────────────────────

  it("comment-preservation modal fires when has_comments is true", async () => {
    const setSpy = vi.spyOn(apiMod.api, "setModel");
    await renderWithFixture(buildFixture({ has_comments: true }));
    const select = screen.getByLabelText("Set curator") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "large" } });
    expect(
      screen.getByRole("dialog", { name: /comment preservation/i }),
    ).toBeInTheDocument();
    // POST hasn't fired yet — gated behind the modal.
    expect(setSpy).not.toHaveBeenCalled();
  });

  it("comment-preservation modal Confirm proceeds with the save", async () => {
    const setSpy = vi
      .spyOn(apiMod.api, "setModel")
      .mockResolvedValue({
        ok: true, subsystem: "curator", value: "large",
        resolved_tier: "large", resolved_model_id: "sonnet",
        backup_path: "/tmp/config.yaml.bak",
      } satisfies ModelSetResponse);
    await renderWithFixture(buildFixture({ has_comments: true }));
    fireEvent.change(
      screen.getByLabelText("Set curator") as HTMLSelectElement,
      { target: { value: "large" } },
    );
    fireEvent.click(screen.getByText(/Confirm \+ back up/i));
    await waitFor(() => expect(setSpy).toHaveBeenCalled());
  });

  it("comment-preservation modal Close cancels the save", async () => {
    const setSpy = vi.spyOn(apiMod.api, "setModel");
    await renderWithFixture(buildFixture({ has_comments: true }));
    fireEvent.change(
      screen.getByLabelText("Set curator") as HTMLSelectElement,
      { target: { value: "large" } },
    );
    fireEvent.click(screen.getByText(/Close \(edit directly\)/i));
    expect(setSpy).not.toHaveBeenCalled();
  });

  it("comment-preservation modal does NOT fire when has_comments is false", async () => {
    const setSpy = vi
      .spyOn(apiMod.api, "setModel")
      .mockResolvedValue({
        ok: true, subsystem: "curator", value: "large",
        resolved_tier: "large", resolved_model_id: "sonnet",
        backup_path: null,
      } satisfies ModelSetResponse);
    await renderWithFixture(buildFixture({ has_comments: false }));
    fireEvent.change(
      screen.getByLabelText("Set curator") as HTMLSelectElement,
      { target: { value: "large" } },
    );
    // POST fires immediately; no modal.
    await waitFor(() => expect(setSpy).toHaveBeenCalled());
    expect(
      screen.queryByRole("dialog", { name: /comment preservation/i }),
    ).not.toBeInTheDocument();
  });

  // ────────────────────────────────────────────────────────────────
  // Reset button per row
  // ────────────────────────────────────────────────────────────────

  it("reset button on a configured row dispatches POST /api/v1/models/reset", async () => {
    const resetSpy = vi
      .spyOn(apiMod.api, "resetModel")
      .mockResolvedValue({
        ok: true, scope: "goal_judge", backup_path: null,
      });
    await renderWithFixture(buildFixture());
    // Only goal_judge has configured: "small" in the fixture, so
    // only that row has the reset button.
    const resetBtn = screen.getByLabelText("Reset goal_judge");
    fireEvent.click(resetBtn);
    await waitFor(() =>
      expect(resetSpy).toHaveBeenCalledWith(
        TOKEN, { subsystem: "goal_judge" },
      ),
    );
  });
});
