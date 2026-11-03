import express from 'express';
import { add, multiply } from './utils/math';
import { UserService } from '@/services/user';
import * as mathNs from './utils/math';
import { shout } from './utils';
import './polyfill';

const app = express();
const service = new UserService();

app.get('/add', (req, res) => {
  res.json({ result: add(1, 2), loud: shout('ok') });
});

app.post('/user', async (req, res) => {
  const u = await service.find(req.body.id);
  res.json(u);
});

function bootstrap() {
  return mathNs.double(multiply(2, 3));
}

app.listen(3000, () => {
  bootstrap();
});
