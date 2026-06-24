import type { Market } from './api';

export function currencySymbol(market: Market): string {
  return market === 'india' ? '₹' : '$';
}

export function formatMarketCap(value: number | undefined | null, market: Market): string {
  if (value == null) return '-';
  if (market === 'india') {
    // Indian numbering system: Crore = 1e7, Lakh Crore = 1e12
    if (value >= 1e12) return `₹${(value / 1e12).toFixed(2)} Lakh Cr`;
    if (value >= 1e7) return `₹${(value / 1e7).toFixed(2)} Cr`;
    if (value >= 1e5) return `₹${(value / 1e5).toFixed(2)} L`;
    return `₹${value.toFixed(0)}`;
  }
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  return `$${value.toFixed(0)}`;
}

export function formatPrice(value: number | undefined | null, market: Market): string {
  if (value == null) return '-';
  return `${currencySymbol(market)}${value.toFixed(2)}`;
}
