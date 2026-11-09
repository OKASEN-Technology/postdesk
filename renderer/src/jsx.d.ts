/** The JSX namespace for `h` (h.ts): any intrinsic element, any props.
 * Satori is the consumer, and it checks the style object at run time. */

import type { Element, Node } from "./h.js";

declare global {
  namespace JSX {
    type Element = import("./h.js").Element | Node;
    interface IntrinsicAttributes { key?: string | number | null }
    interface IntrinsicElements {
      [tag: string]: Record<string, unknown>;
    }
    interface ElementChildrenAttribute {
      children: unknown;
    }
  }
}

export type { Element };
