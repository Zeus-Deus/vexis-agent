// Vitest global setup. Day 3 of model UX wires vitest +
// @testing-library/react + jsdom for the dashboard's first
// component tests. Imports the jest-dom matchers so tests can use
// `toBeInTheDocument()`, `toHaveTextContent()`, etc.

import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Auto-unmount React trees after each test so DOM doesn't leak.
afterEach(() => {
  cleanup();
});
