'use client';

import {
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import ShoppingCartIcon from '@mui/icons-material/ShoppingCart';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { tradingApi, type ManualOrderInput } from '@/lib/api/endpoints';
import { useAuth } from '@/lib/auth/AuthProvider';
import { apiErrorMessage } from '@/lib/api/client';

const SYMBOL_LIST = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'AVAX/USDT', 'MATIC/USDT'];

interface Props {
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}

interface OrderForm {
  symbol: string;
  customSymbol: string;
  useCustomSymbol: boolean;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit';
  quantity: string;
  price: string;
}

const DEFAULT_FORM: OrderForm = {
  symbol: 'BTC/USDT',
  customSymbol: '',
  useCustomSymbol: false,
  side: 'buy',
  order_type: 'market',
  quantity: '',
  price: '',
};

export function ManualOrderCard({ onSuccess, onError }: Props) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [form, setForm] = useState<OrderForm>(DEFAULT_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof OrderForm, string>>>({});

  const canOrder = can('trading.manual_order');

  const mutation = useMutation({
    mutationFn: (input: ManualOrderInput) => tradingApi.placeOrder(input),
    onSuccess: (trade) => {
      qc.invalidateQueries({ queryKey: ['portfolio', 'positions'] });
      qc.invalidateQueries({ queryKey: ['portfolio', 'trades'] });
      onSuccess(`Ordre ${trade.side.toUpperCase()} ${trade.quantity} ${trade.symbol} placé`);
      setForm(DEFAULT_FORM);
      setErrors({});
    },
    onError: (err) => onError(apiErrorMessage(err, "Erreur lors du placement de l'ordre")),
  });

  function validate(): boolean {
    const newErrors: Partial<Record<keyof OrderForm, string>> = {};
    const symbol = form.useCustomSymbol ? form.customSymbol.trim() : form.symbol;
    if (!symbol) newErrors.symbol = 'Symbole requis';

    const qty = parseFloat(form.quantity);
    if (!form.quantity || isNaN(qty) || qty <= 0) {
      newErrors.quantity = 'Quantité invalide (> 0)';
    }

    if (form.order_type === 'limit') {
      const price = parseFloat(form.price);
      if (!form.price || isNaN(price) || price <= 0) {
        newErrors.price = 'Prix invalide (> 0)';
      }
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  function handleSubmit() {
    if (!canOrder || !validate()) return;
    const symbol = form.useCustomSymbol ? form.customSymbol.trim() : form.symbol;
    const input: ManualOrderInput = {
      symbol,
      side: form.side,
      order_type: form.order_type,
      quantity: parseFloat(form.quantity),
      ...(form.order_type === 'limit' ? { price: parseFloat(form.price) } : {}),
    };
    mutation.mutate(input);
  }

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
          <ShoppingCartIcon color="primary" />
          <Typography variant="h6">Ordre manuel</Typography>
        </Stack>

        <Stack spacing={2}>
          {/* Symbol */}
          <Box>
            <FormControl fullWidth size="small">
              <InputLabel>Symbole</InputLabel>
              <Select
                value={form.useCustomSymbol ? '__custom__' : form.symbol}
                label="Symbole"
                disabled={!canOrder}
                onChange={(e) => {
                  const val = e.target.value as string;
                  if (val === '__custom__') {
                    setForm((f) => ({ ...f, useCustomSymbol: true }));
                  } else {
                    setForm((f) => ({ ...f, symbol: val, useCustomSymbol: false }));
                  }
                  setErrors((e2) => ({ ...e2, symbol: undefined }));
                }}
              >
                {SYMBOL_LIST.map((s) => (
                  <MenuItem key={s} value={s}>{s}</MenuItem>
                ))}
                <MenuItem value="__custom__">Autre (saisie libre)…</MenuItem>
              </Select>
            </FormControl>
            {form.useCustomSymbol && (
              <TextField
                fullWidth
                size="small"
                placeholder="ex: XRP/USDT"
                value={form.customSymbol}
                onChange={(e) => setForm((f) => ({ ...f, customSymbol: e.target.value }))}
                error={!!errors.symbol}
                helperText={errors.symbol}
                disabled={!canOrder}
                sx={{ mt: 1 }}
              />
            )}
          </Box>

          {/* Side */}
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
              Sens
            </Typography>
            <Tooltip title={!canOrder ? 'Permission requise' : ''} disableHoverListener={canOrder}>
              <span>
                <ToggleButtonGroup
                  value={form.side}
                  exclusive
                  onChange={(_, val) => val && setForm((f) => ({ ...f, side: val }))}
                  size="small"
                  disabled={!canOrder}
                  fullWidth
                >
                  <ToggleButton value="buy" sx={{ color: 'success.main', '&.Mui-selected': { bgcolor: 'success.main', color: '#fff' } }}>
                    Acheter
                  </ToggleButton>
                  <ToggleButton value="sell" sx={{ color: 'error.main', '&.Mui-selected': { bgcolor: 'error.main', color: '#fff' } }}>
                    Vendre
                  </ToggleButton>
                </ToggleButtonGroup>
              </span>
            </Tooltip>
          </Box>

          {/* Order type */}
          <FormControl fullWidth size="small">
            <InputLabel>Type d'ordre</InputLabel>
            <Select
              value={form.order_type}
              label="Type d'ordre"
              disabled={!canOrder}
              onChange={(e) => setForm((f) => ({ ...f, order_type: e.target.value as 'market' | 'limit', price: '' }))}
            >
              <MenuItem value="market">Market</MenuItem>
              <MenuItem value="limit">Limit</MenuItem>
            </Select>
          </FormControl>

          {/* Quantity */}
          <TextField
            fullWidth
            size="small"
            label="Quantité"
            type="number"
            inputProps={{ min: 0, step: 'any' }}
            value={form.quantity}
            onChange={(e) => {
              setForm((f) => ({ ...f, quantity: e.target.value }));
              setErrors((e2) => ({ ...e2, quantity: undefined }));
            }}
            error={!!errors.quantity}
            helperText={errors.quantity}
            disabled={!canOrder}
          />

          {/* Price (limit only) */}
          {form.order_type === 'limit' && (
            <TextField
              fullWidth
              size="small"
              label="Prix (USD)"
              type="number"
              inputProps={{ min: 0, step: 'any' }}
              value={form.price}
              onChange={(e) => {
                setForm((f) => ({ ...f, price: e.target.value }));
                setErrors((e2) => ({ ...e2, price: undefined }));
              }}
              error={!!errors.price}
              helperText={errors.price}
              disabled={!canOrder}
            />
          )}

          {/* Submit */}
          <Tooltip title={!canOrder ? 'Permission requise' : ''} disableHoverListener={canOrder}>
            <span>
              <Button
                fullWidth
                variant="contained"
                color={form.side === 'buy' ? 'success' : 'error'}
                onClick={handleSubmit}
                disabled={!canOrder || mutation.isPending}
                sx={{ mt: 1 }}
              >
                {mutation.isPending ? (
                  <CircularProgress size={20} color="inherit" />
                ) : (
                  `${form.side === 'buy' ? 'Acheter' : 'Vendre'} — ${form.order_type.toUpperCase()}`
                )}
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </CardContent>
    </Card>
  );
}
