// Unsigned beta builds still need a complete ad-hoc signature over the whole bundle. Without
// it, Apple Silicon Macs call a downloaded copy "damaged" and offer no "Open Anyway".
// (electron-builder's own ad-hoc signing fails: it asks for a timestamp, which ad-hoc can't have.)
const { execFileSync } = require('node:child_process')
const path = require('node:path')

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  // Downloaded or copied files carry extended attributes (provenance, Finder info) that
  // codesign rejects as "detritus", so clear them first.
  execFileSync('xattr', ['-cr', app], { stdio: 'inherit' })
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', app], { stdio: 'inherit' })
  execFileSync('xattr', ['-c', app], { stdio: 'inherit' })  // Finder tags the bundle folder again after signing
  execFileSync('codesign', ['--verify', '--deep', '--strict', app], { stdio: 'inherit' })
}
