export function add(a: number, b: number): number {
  return a + b;
}

export function multiply(a: number, b: number): number {
  return a * b;
}

export const double = (x: number) => multiply(x, 2);

export function addWrapper(a: number, b: number) {
  return add(a, b);
}

export function neverUsed(xs: number[]) {
  let total = 0;
  for (const x of xs) {
    if (x > 0) {
      total += x;
    } else {
      total -= x;
    }
  }
  return total;
}

export function scaleAbove(items: number[], limit: number) {
  const out: number[] = [];
  for (const item of items) {
    if (item > limit) {
      out.push(item * 2);
    } else {
      out.push(item);
    }
  }
  return out.sort();
}

export function scaleAboveCopy(values: number[], max: number) {
  const acc: number[] = [];
  for (const value of values) {
    if (value > max) {
      acc.push(value * 2);
    } else {
      acc.push(value);
    }
  }
  return acc.sort();
}

function internalHelper(x: number) {
  return x;
}

export function todo() {
  throw new Error('not implemented');
}

export default function compute(x: number) {
  return addWrapper(x, x);
}
