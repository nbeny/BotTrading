import { describe, expect, it } from 'vitest';
import { axisValue } from '../dossier';

describe('axisValue', () => {
  it('rend la valeur d’un axe mesuré', () => {
    expect(axisValue({ positioning: 0.93 }, 'positioning')).toBe(0.93);
  });

  it('rend null — et non 0 — pour un axe absent', () => {
    expect(axisValue({ positioning: 0.93 }, 'fundamentals')).toBeNull();
  });

  it('conserve un zéro mesuré', () => {
    expect(axisValue({ volume_growth: 0 }, 'volume_growth')).toBe(0);
  });
});
