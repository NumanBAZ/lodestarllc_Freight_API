# US ZIP location data

`us_zip_codes.json` is a local ZIP-to-city/state mapping derived from the USPS
ZIP Locale Detail file. The distributable JSON and generation tooling are from
the MIT-licensed `pseudosavant/USPSZIPCodes` project:

https://github.com/pseudosavant/USPSZIPCodes

The application loads this file only on the backend. It is not embedded in the
public or staff JavaScript bundles.
