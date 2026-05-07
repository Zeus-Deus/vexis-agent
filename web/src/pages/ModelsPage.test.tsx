// Day 3 of model UX — ModelsPage component tests.
//
// Mocks the api.models() fetch call so the page renders against a
// representative resolution table without hitting the backend.
// Cases per the audit ask:
//   - renders all 8 subsystems from DEFAULT_SUBSYSTEM_TIERS
//   - validator findings render with severity-appropriate glyph
//   - tier-overrides section collapses and expands
//   - dead-knob info finding renders without breaking the layout
// Plus a few cross-cutting smokes (brain banner, empty state).

import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ModelsPage } from "./ModelsPage";
import type { ModelsState } from "../lib/types";
import * as apiMod from "../lib/api";

const TOKEN = "test-token";

// ──────────────────────────────────────────────────────────────────
// Fixtures
// ──────────────────────────────────────────────────────────────────

// Representative resolution table: every known subsystem, the
// dead-knob info finding on migration_classifier, one rule-4
// error on goal_judge (simulates an opencode bare-alias trap),
// one tier override.
function buildFixture(
  overrides: Partial<ModelsState> = {},
): ModelsState {
  return {
    brain_kind: "claude-code",
    subsystems: [
      {
        name: "coherence_judge",
        configured: null,
        resolved_tier: "small",
        resolved_model_id: "haiku",
        findings: [],
      },
      {
        name: "curator",
        configured: null,
        resolved_tier: "small",
        resolved_model_id: "haiku",
        findings: [],
      },
      {
        name: "goal_judge",
        configured: "sonnet",
        resolved_tier: "sonnet",
        resolved_model_id: "sonnet",
        findings: [
          {
            severity: "error",
            subsystem: "goal_judge",
            problem: "goal_judge resolves to bare alias 'sonnet' on opencode",
            suggested_fix: "Run /model set goal_judge large",
          },
        ],
      },
      {
        name: "learning_review",
        configured: null,
        resolved_tier: "small",
        resolved_model_id: "haiku",
        findings: [],
      },
      {
        name: "learning_triage",
        configured: null,
        resolved_tier: "tiny",
        resolved_model_id: "haiku",
        findings: [],
      },
      {
        name: "migration_classifier",
        configured: null,
        resolved_tier: "small",
        resolved_model_id: "haiku",
        findings: [
          {
            severity: "info",
            subsystem: "migration_classifier",
            problem:
              "migration_classifier declared in DEFAULT_SUBSYSTEM_TIERS but no live spawn caller reads it.",
            suggested_fix: "safe to remove",
          },
        ],
      },
      {
        name: "relationships_classifier",
        configured: null,
        resolved_tier: "tiny",
        resolved_model_id: "haiku",
        findings: [],
      },
      {
        name: "relationships_extractor",
        configured: null,
        resolved_tier: "medium",
        resolved_model_id: "sonnet",
        findings: [],
      },
    ],
    tier_overrides: {
      tiny: { configured: null, default: "haiku" },
      small: { configured: null, default: "haiku" },
      medium: { configured: "opus", default: "sonnet" },  // overridden
      large: { configured: null, default: "sonnet" },
    },
    brain_inventory: ["claude-code", "null", "opencode"],
    global_findings: [],
    // Day 4 additions — defaults for the read-only test cases.
    available_models: {
      "claude-code": ["haiku", "sonnet", "opus"],
      opencode: [],
      null: [],
    },
    // Day 1 of model picker UX — provider-grouped sibling.
    // Dashboard (Day 2) reads this for the <optgroup>-grouped
    // dropdown; flat available_models above stays for backwards
    // compat consumers.
    available_models_by_provider: {
      "claude-code": { anthropic: ["haiku", "sonnet", "opus"] },
      opencode: {},
      null: {},
    },
    has_comments: false,
    model_ux_enabled: false,
    ...overrides,
  };
}

// ──────────────────────────────────────────────────────────────────
// Test setup — mock api.models, no real fetch
// ──────────────────────────────────────────────────────────────────

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderWithFixture(state: ModelsState) {
  const onAuthFail = vi.fn();
  const apiSpy = vi.spyOn(apiMod.api, "models").mockResolvedValue(state);
  render(<ModelsPage token={TOKEN} onAuthFail={onAuthFail} />);
  // Page kicks off a fetch in useEffect; wait for the spy + a
  // unique anchor in the post-load DOM. "Subsystem resolution" is
  // a section title that only appears once and only after the
  // fixture lands.
  await waitFor(() => expect(apiSpy).toHaveBeenCalled());
  await screen.findByText("Subsystem resolution");
  return { onAuthFail, apiSpy };
}

// ──────────────────────────────────────────────────────────────────
// Renders all 8 subsystems
// ──────────────────────────────────────────────────────────────────

describe("ModelsPage", () => {
  it("renders all 8 subsystems from the resolution table", async () => {
    await renderWithFixture(buildFixture());
    const expectedNames = [
      "coherence_judge",
      "curator",
      "goal_judge",
      "learning_review",
      "learning_triage",
      "migration_classifier",
      "relationships_classifier",
      "relationships_extractor",
    ];
    for (const name of expectedNames) {
      expect(
        await screen.findByText(name),
        `subsystem ${name} not rendered`,
      ).toBeInTheDocument();
    }
  });

  it("renders the brain banner with the active brain.kind", async () => {
    await renderWithFixture(buildFixture({ brain_kind: "opencode" }));
    expect(await screen.findByText("opencode")).toBeInTheDocument();
    // Brain section header rendered.
    expect(screen.getByText("Brain")).toBeInTheDocument();
  });

  // ────────────────────────────────────────────────────────────────
  // Validator findings — severity glyphs
  // ────────────────────────────────────────────────────────────────

  it("renders error severity with the ✗ glyph on goal_judge row", async () => {
    await renderWithFixture(buildFixture());
    // Find the goal_judge row's status cell (the table layout puts
    // status in the rightmost grid column). Easiest assertion: the
    // rendered DOM contains a ✗ glyph somewhere, and the goal_judge
    // row's row text includes the model id.
    expect(screen.getAllByText("✗").length).toBeGreaterThan(0);
    // The error message tooltip text uses the validator's wording.
    const errorGlyph = screen.getAllByText("✗")[0];
    expect(errorGlyph.getAttribute("title")).toContain(
      "bare alias 'sonnet' on opencode",
    );
  });

  it("renders dead-knob info finding without breaking the layout", async () => {
    await renderWithFixture(buildFixture());
    // migration_classifier row renders its name (the layout
    // survived the info-level finding).
    expect(screen.getByText("migration_classifier")).toBeInTheDocument();
    // The ⓘ glyph appears for the info-level finding.
    expect(screen.getAllByText("ⓘ").length).toBeGreaterThan(0);
  });

  it("renders ✓ for subsystems with no findings", async () => {
    await renderWithFixture(buildFixture());
    // 6 of the 8 fixture subsystems have no findings (all except
    // goal_judge and migration_classifier).
    expect(screen.getAllByText("✓").length).toBeGreaterThanOrEqual(6);
  });

  // ────────────────────────────────────────────────────────────────
  // Tier overrides — collapsible
  // ────────────────────────────────────────────────────────────────

  it("collapses tier overrides by default and expands on click", async () => {
    await renderWithFixture(buildFixture());
    // Toggle button is present.
    const toggle = screen.getByRole("button", { name: /per-tier mapping/i });
    expect(toggle).toBeInTheDocument();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // Tier rows are NOT in the DOM yet — collapsed.
    expect(screen.queryByText("tiny")).not.toBeInTheDocument();

    // Click to expand.
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    // Now tier rows are visible.
    expect(screen.getByText("tiny")).toBeInTheDocument();
    expect(screen.getByText("small")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
    expect(screen.getByText("large")).toBeInTheDocument();
    // Overridden value (medium → opus) renders.
    expect(screen.getByText("opus")).toBeInTheDocument();
  });

  it("shows '(none set)' when no tier overrides are configured", async () => {
    const fixture = buildFixture({
      tier_overrides: {
        tiny: { configured: null, default: "haiku" },
        small: { configured: null, default: "haiku" },
        medium: { configured: null, default: "sonnet" },
        large: { configured: null, default: "sonnet" },
      },
    });
    await renderWithFixture(fixture);
    expect(screen.getByText(/none set/i)).toBeInTheDocument();
  });

  it("shows the override count when tier overrides are present", async () => {
    await renderWithFixture(buildFixture()); // 1 override (medium)
    expect(screen.getByText(/1 overridden/i)).toBeInTheDocument();
  });

  // ────────────────────────────────────────────────────────────────
  // Available models hint — per-brain copy
  // ────────────────────────────────────────────────────────────────

  it("shows claude-code aliases in the available-models hint", async () => {
    await renderWithFixture(buildFixture());
    // The hint mentions sonnet/opus/haiku for claude-code.
    expect(screen.getByText(/Aliases: sonnet, opus, haiku/)).toBeInTheDocument();
  });

  it("shows opencode discovery hint when brain.kind=opencode", async () => {
    await renderWithFixture(buildFixture({ brain_kind: "opencode" }));
    expect(screen.getByText(/provider\/model/i)).toBeInTheDocument();
  });

  // ────────────────────────────────────────────────────────────────
  // Global findings panel
  // ────────────────────────────────────────────────────────────────

  it("renders global validator findings when present", async () => {
    const fixture = buildFixture({
      global_findings: [
        {
          severity: "warning",
          subsystem: null,
          problem: "brain.kind='claudecode' is not a recognised brain.",
          suggested_fix: "Set brain.kind to claude-code or opencode.",
        },
      ],
    });
    await renderWithFixture(fixture);
    expect(
      screen.getByText(/brain.kind='claudecode'/),
    ).toBeInTheDocument();
  });

  it("hides the global findings panel when none are present", async () => {
    await renderWithFixture(buildFixture()); // global_findings: []
    expect(
      screen.queryByText(/Validator \(whole config\)/i),
    ).not.toBeInTheDocument();
  });
});
