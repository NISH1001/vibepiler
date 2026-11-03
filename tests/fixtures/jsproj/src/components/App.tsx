import React, { useState } from 'react';
import Header from './Header';
import { double } from '@/utils/math';

export default function App() {
  const [count, setCount] = useState(0);
  return (
    <div>
      <Header title={`count ${double(count)}`} />
      <button onClick={() => setCount(count + 1)}>+</button>
    </div>
  );
}
