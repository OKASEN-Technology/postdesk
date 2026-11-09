/**
 * The JSX factory the slide layouts compile to (tsconfig `jsxFactory: "h"`).
 * Satori takes React-shaped element objects, `{type, props}` with the
 * children on `props.children`, so no React is needed: `h` builds exactly
 * that, flattening nested arrays and dropping `null`, `false` and `undefined`
 * children the way React does.
 */

export type Node = Element | string | number | null | undefined | false | Node[];

export interface Element {
  type: string | ((props: Record<string, unknown>) => Node);
  props: Record<string, unknown> & { children?: Node };
  key?: string | number | null;
}

function flatten(children: Node[], out: Node[] = []): Node[] {
  for (const c of children) {
    if (Array.isArray(c)) flatten(c, out);
    else if (c !== null && c !== undefined && c !== false) out.push(c);
  }
  return out;
}

export function h(type: Element["type"], props: Record<string, unknown> | null, ...children: Node[]): Node {
  const { key = null, ...rest } = props ?? {};
  const kids = flatten(children);
  const merged: Element["props"] = { ...rest };
  // The layouts size boxes the CSS way (padding inside the width); satori's
  // default is content-box, so say so once here rather than on every div.
  const style = merged.style as Record<string, unknown> | undefined;
  if (style && typeof style === "object" && !("boxSizing" in style)) merged.style = { boxSizing: "border-box", ...style };
  if (kids.length === 1) merged.children = kids[0];
  else if (kids.length > 1) merged.children = kids;
  // A function component is expanded here, so satori only ever sees
  // intrinsic elements.
  if (typeof type === "function") return type(merged);
  return { type, props: merged, key: key as string | number | null };
}

export function Fragment(props: { children?: Node }): Node {
  return props.children ?? null;
}
