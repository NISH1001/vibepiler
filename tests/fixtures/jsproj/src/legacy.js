const math = require('./utils/math');
const { add } = require('./utils/math');

function legacySum(a, b) {
  return math.add(a, b) + add(a, b);
}

module.exports = { legacySum };
