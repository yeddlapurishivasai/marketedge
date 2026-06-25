import { createContext } from 'react';

export const ThemeContext = createContext<{ theme: string; toggle: () => void }>({
  theme: 'light',
  toggle: () => {},
});
