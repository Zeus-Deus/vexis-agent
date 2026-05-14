// ComputerUsePage component tests.
//
// Mocks api.computerUseSettings / computerUseSettingsSet so the page
// renders + saves against a representative payload without a backend.
// Cases:
//   - renders the three sections + the live-activity readout
//   - dynamic section is collapsed until the toggle is flipped
//   - flipping the toggle reveals the threshold + dynamic model picker
//   - picking a model and saving threads the right partial update
//   - the "rich tree" activity verdict renders
//   - a null last_activity renders the friendly empty state

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ComputerUsePage } from "./ComputerUsePage";
import type {
  ComputerUseSettings,
  ComputerUseSettingsResponse,
} from "../lib/types";
import * as apiMod from "../lib/api";

const TOKEN = "test-token";

function buildFixture(
  overrides: Partial<ComputerUseSettings> = {},
): ComputerUseSettings {
  return {
    model: "",
    reasoning_level: "",
    dynamic: {
      enabled: false,
      model: "",
      reasoning_level: "",
      min_elements: 5,
    },
    available_models: [
      {
        id: "claude-haiku-4-5",
        display_name: "Claude Haiku 4.5",
        reasoning_levels: [],
        max_input_tokens: 200000,
        max_tokens: 8192,
        provider: "anthropic",
        free: false,
        cost_input_per_million: null,
        cost_output_per_million: null,
      },
      {
        id: "claude-sonnet-4-6",
        display_name: "Claude Sonnet 4.6",
        reasoning_levels: [],
        max_input_tokens: 200000,
        max_tokens: 64000,
        provider: "anthropic",
        free: false,
        cost_input_per_million: null,
        cost_output_per_million: null,
      },
    ],
    last_activity: {
      element_count: 12,
      used_vision_fallback: false,
      stale: false,
      age_seconds: 8,
      fresh: true,
      rich: true,
    },
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderWithFixture(state: ComputerUseSettings) {
  const onAuthFail = vi.fn();
  const getSpy = vi
    .spyOn(apiMod.api, "computerUseSettings")
    .mockResolvedValue(state);
  const setSpy = vi
    .spyOn(apiMod.api, "computerUseSettingsSet")
    .mockResolvedValue({
      ...state,
      ok: true,
      backup_path: null,
    } as ComputerUseSettingsResponse);
  render(<ComputerUsePage token={TOKEN} onAuthFail={onAuthFail} />);
  await waitFor(() => expect(getSpy).toHaveBeenCalled());
  await screen.findByText("Computer-use model");
  return { onAuthFail, getSpy, setSpy };
}

describe("ComputerUsePage", () => {
  it("renders the core sections + activity readout", async () => {
    await renderWithFixture(buildFixture());
    expect(screen.getByText("Computer-use model")).toBeInTheDocument();
    expect(screen.getByText("Dynamic model switching")).toBeInTheDocument();
    expect(screen.getByText("Live activity")).toBeInTheDocument();
    // Rich + fresh fixture → the "dynamic model would apply" verdict.
    expect(
      screen.getByText(/Rich tree — dynamic model would apply/),
    ).toBeInTheDocument();
  });

  it("keeps the dynamic controls hidden until the toggle is on", async () => {
    await renderWithFixture(buildFixture());
    // Threshold field is part of the dynamic block — absent while off.
    expect(screen.queryByText("Richness threshold")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("switch", { name: /Dynamic switching/ }),
    );

    expect(await screen.findByText("Richness threshold")).toBeInTheDocument();
    // The dynamic model picker carries its own intro copy.
    expect(
      screen.getByText(/fast model used when the last snapshot/),
    ).toBeInTheDocument();
  });

  it("threads a partial update through computerUseSettingsSet on save", async () => {
    const { setSpy } = await renderWithFixture(buildFixture());

    // Pick the pinned model — radio label is the display name.
    fireEvent.click(
      screen.getByRole("radio", { name: /Claude Haiku 4\.5/ }),
    );

    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() => expect(setSpy).toHaveBeenCalledTimes(1));
    const [, body] = setSpy.mock.calls[0];
    expect(body.model).toBe("claude-haiku-4-5");
    // Switching the model also resets its reasoning pick.
    expect(body.reasoning_level).toBe("");
  });

  it("saves the dynamic enable flag", async () => {
    const { setSpy } = await renderWithFixture(buildFixture());

    fireEvent.click(
      screen.getByRole("switch", { name: /Dynamic switching/ }),
    );
    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() => expect(setSpy).toHaveBeenCalledTimes(1));
    const [, body] = setSpy.mock.calls[0];
    expect(body.dynamic?.enabled).toBe(true);
  });

  it("renders a friendly empty state when there is no activity", async () => {
    await renderWithFixture(buildFixture({ last_activity: null }));
    expect(
      screen.getByText(/snapshot recorded yet/),
    ).toBeInTheDocument();
  });
});
