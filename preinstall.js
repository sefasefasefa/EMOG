const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Remove lockfiles
try { fs.unlinkSync(path.join(__dirname, 'package-lock.json')); } catch(e) {}
try { fs.unlinkSync(path.join(__dirname, 'yarn.lock')); } catch(e) {}

// Check pnpm
const ua = process.env.npm_config_user_agent || '';
if (!ua.startsWith('pnpm')) {
  process.stderr.write('Use pnpm instead\n');
  process.exit(1);
}
