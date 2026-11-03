import { describe, it, expect } from 'vitest';
import compute, { add, scaleAbove } from '../utils/math';

describe('math', () => {
  it('adds', () => {
    expect(add(1, 2)).toBe(3);
  });
  it('computes', () => {
    expect(compute(2)).toBe(4);
    expect(scaleAbove([1, 2], 1)).toEqual([1, 4]);
  });
});
