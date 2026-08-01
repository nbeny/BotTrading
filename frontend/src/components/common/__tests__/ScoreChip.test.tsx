import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ScoreChip } from '../index';

describe('ScoreChip', () => {
  it('rend un score 0–1 en pourcentage entier', () => {
    render(<ScoreChip score={0.79} />);
    expect(screen.getByText('79')).toBeInTheDocument();
  });

  it('colore selon le score réel, pas selon la valeur brute', () => {
    // 0.79 -> 79 -> success. Avant correction, scoreColor(0.79) tombait dans
    // la branche `> 0` et rendait tout en rouge.
    const { container } = render(<ScoreChip score={0.79} />);
    expect(container.querySelector('.MuiChip-colorSuccess')).not.toBeNull();
  });

  it('distingue un zéro mesuré d’un score faible', () => {
    render(<ScoreChip score={0} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });
});
