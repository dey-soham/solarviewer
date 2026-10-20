"""
Image metadata extraction (FITS headers / CASA image info) and formatting.
"""

import numpy as np

from .dependencies import ASTROPY_AVAILABLE, CASA_AVAILABLE, IA, u


def get_image_metadata(imagename):
    """
    Extract structured metadata from a FITS or CASA image file.

    Returns a dictionary with organized metadata categories:
    - observation: Date, telescope, observer, object
    - spectral: Frequency, bandwidth, spectral system
    - beam: Major/minor axis, position angle
    - image: Dimensions, pixel scale, reference position, units
    - processing: Origin software, version, weighting, history

    Also returns a formatted text representation for display.
    """
    metadata = {
        "observation": {},
        "spectral": {},
        "beam": {},
        "image": {},
        "processing": {},
        "raw_header": {},
    }

    def format_frequency(freq_hz):
        """Format frequency in appropriate units."""
        if freq_hz is None:
            return None
        freq_hz = float(freq_hz)
        if freq_hz >= 1e9:
            return f"{freq_hz / 1e9:.4f} GHz"
        elif freq_hz >= 1e6:
            return f"{freq_hz / 1e6:.3f} MHz"
        elif freq_hz >= 1e3:
            return f"{freq_hz / 1e3:.2f} kHz"
        else:
            return f"{freq_hz:.2f} Hz"

    def format_angle_arcsec(angle_deg):
        """Format angle from degrees to arcsec/arcmin as appropriate."""
        if angle_deg is None:
            return None
        angle_arcsec = abs(float(angle_deg)) * 3600
        if angle_arcsec >= 60:
            return f"{angle_arcsec / 60:.3f} arcmin"
        else:
            return f"{angle_arcsec:.3f} arcsec"

    def format_ra_hms(ra_deg):
        """Format RA in hours:minutes:seconds."""
        if ra_deg is None:
            return None
        try:
            if ASTROPY_AVAILABLE:
                from astropy.coordinates import SkyCoord

                coord = SkyCoord(ra=float(ra_deg) * u.degree, dec=0 * u.degree)
                return coord.ra.to_string(unit=u.hour, sep=":", precision=2)
            else:
                # Manual conversion
                ra_hours = float(ra_deg) / 15.0
                h = int(ra_hours)
                m = int((ra_hours - h) * 60)
                s = ((ra_hours - h) * 60 - m) * 60
                return f"{h:02d}:{m:02d}:{s:05.2f}"
        except:
            return f"{ra_deg:.6f}°"

    def format_dec_dms(dec_deg):
        """Format Dec in degrees:arcmin:arcsec."""
        if dec_deg is None:
            return None
        try:
            if ASTROPY_AVAILABLE:
                from astropy.coordinates import SkyCoord

                coord = SkyCoord(ra=0 * u.degree, dec=float(dec_deg) * u.degree)
                return coord.dec.to_string(sep=":", precision=2)
            else:
                # Manual conversion
                sign = "+" if dec_deg >= 0 else "-"
                dec_deg = abs(float(dec_deg))
                d = int(dec_deg)
                m = int((dec_deg - d) * 60)
                s = ((dec_deg - d) * 60 - m) * 60
                return f"{sign}{d:02d}:{m:02d}:{s:05.2f}"
        except:
            return f"{dec_deg:.6f}°"

    def format_datetime(date_str):
        """Format observation date/time nicely."""
        if not date_str:
            return None
        try:
            from datetime import datetime

            # Handle various date formats
            date_str = str(date_str).strip()
            # Try ISO format with fractional seconds
            if "." in date_str:
                # Handle fractional seconds by truncating
                base, frac = date_str.rsplit(".", 1)
                # Limit fractional seconds to 6 digits for microseconds
                frac = frac[:6].ljust(6, "0")
                date_str = f"{base}.{frac}"
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%f")
            else:
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except:
            return str(date_str)

    is_fits = imagename.endswith(".fits") or imagename.endswith(".fts")

    try:
        if is_fits and ASTROPY_AVAILABLE:
            # Use astropy for FITS files
            from astropy.io import fits

            with fits.open(imagename) as hdul:
                header = hdul[0].header

                # Store raw header for reference
                for key in header.keys():
                    if key and key not in ("", "COMMENT", "HISTORY"):
                        try:
                            metadata["raw_header"][key] = str(header[key])
                        except:
                            pass

                # Observation info
                metadata["observation"]["Date/Time"] = format_datetime(
                    header.get("DATE-OBS")
                )
                metadata["observation"]["Telescope"] = header.get(
                    "TELESCOP", header.get("INSTRUME")
                )
                metadata["observation"]["Observer"] = header.get("OBSERVER")
                metadata["observation"]["Object"] = header.get("OBJECT")
                metadata["observation"]["Origin"] = header.get("ORIGIN")

                # Spectral info
                freq_hz = None
                for key in ["CRVAL3", "CRVAL4", "RESTFRQ", "FREQ"]:
                    if key in header:
                        try:
                            val = float(header[key])
                            if val > 1e6:  # Likely Hz
                                freq_hz = val
                                break
                        except:
                            pass
                if freq_hz:
                    metadata["spectral"]["Frequency"] = format_frequency(freq_hz)
                    metadata["spectral"]["Frequency (Hz)"] = f"{freq_hz:.0f}"
                    # Wavelength
                    c = 299792458  # m/s
                    wavelength_m = c / freq_hz
                    if wavelength_m >= 1:
                        metadata["spectral"]["Wavelength"] = f"{wavelength_m:.2f} m"
                    elif wavelength_m >= 0.01:
                        metadata["spectral"][
                            "Wavelength"
                        ] = f"{wavelength_m * 100:.2f} cm"
                    else:
                        metadata["spectral"][
                            "Wavelength"
                        ] = f"{wavelength_m * 1000:.3f} mm"

                metadata["spectral"]["Spectral System"] = header.get("SPECSYS")

                # Beam info
                if "BMAJ" in header and "BMIN" in header:
                    bmaj_arcsec = float(header["BMAJ"]) * 3600
                    bmin_arcsec = float(header["BMIN"]) * 3600

                    # Smart beam axis formatting
                    def smart_beam_format(arcsec_val):
                        if arcsec_val >= 60:
                            return f"{arcsec_val / 60:.2f}'"
                        else:
                            return f'{arcsec_val:.2f}"'

                    metadata["beam"]["Major Axis"] = smart_beam_format(bmaj_arcsec)
                    metadata["beam"]["Minor Axis"] = smart_beam_format(bmin_arcsec)
                    metadata["beam"][
                        "Beam Area"
                    ] = f"{np.pi * bmaj_arcsec * bmin_arcsec / (4 * np.log(2)):.2f} arcsec²"
                    if "BPA" in header:
                        metadata["beam"][
                            "Position Angle"
                        ] = f"{float(header['BPA']):.1f}°"

                # Image properties
                naxis1 = header.get("NAXIS1", 0)
                naxis2 = header.get("NAXIS2", 0)
                metadata["image"]["Dimensions"] = f"{naxis1} × {naxis2} pixels"

                if "CDELT1" in header and "CDELT2" in header:
                    # Check CUNIT to determine if CDELT is in degrees or arcsec
                    cunit1 = header.get("CUNIT1", "deg").strip().lower()
                    cunit2 = header.get("CUNIT2", "deg").strip().lower()

                    cdelt1 = abs(float(header["CDELT1"]))
                    cdelt2 = abs(float(header["CDELT2"]))

                    # Convert to arcsec based on the actual unit
                    if cunit1 in ("arcsec", "arcseconds"):
                        cdelt1_arcsec = cdelt1
                    elif cunit1 in ("arcmin", "arcminutes"):
                        cdelt1_arcsec = cdelt1 * 60
                    else:  # deg or unrecognized → assume degrees
                        cdelt1_arcsec = cdelt1 * 3600

                    if cunit2 in ("arcsec", "arcseconds"):
                        cdelt2_arcsec = cdelt2
                    elif cunit2 in ("arcmin", "arcminutes"):
                        cdelt2_arcsec = cdelt2 * 60
                    else:
                        cdelt2_arcsec = cdelt2 * 3600

                    # Smart pixel scale formatting
                    def smart_angle_format(arcsec_val):
                        """Format angle in appropriate units."""
                        if arcsec_val >= 3600:
                            return f"{arcsec_val / 3600:.3f}°"
                        elif arcsec_val >= 60:
                            return f"{arcsec_val / 60:.3f}'"
                        else:
                            return f'{arcsec_val:.3f}"'

                    ps1 = smart_angle_format(cdelt1_arcsec)
                    ps2 = smart_angle_format(cdelt2_arcsec)
                    metadata["image"]["Pixel Scale"] = f"{ps1} × {ps2} /pixel"

                    # Smart FOV formatting
                    fov1_arcsec = cdelt1_arcsec * naxis1
                    fov2_arcsec = cdelt2_arcsec * naxis2
                    fov1 = smart_angle_format(fov1_arcsec)
                    fov2 = smart_angle_format(fov2_arcsec)
                    metadata["image"]["Field of View"] = f"{fov1} × {fov2}"

                metadata["image"][
                    "Coordinate Type"
                ] = f"{header.get('CTYPE1', 'N/A')}, {header.get('CTYPE2', 'N/A')}"

                if "CRVAL1" in header and "CRVAL2" in header:
                    ra_deg = float(header["CRVAL1"])
                    dec_deg = float(header["CRVAL2"])
                    metadata["image"]["Reference RA"] = format_ra_hms(ra_deg)
                    metadata["image"]["Reference Dec"] = format_dec_dms(dec_deg)

                metadata["image"]["Units"] = header.get("BUNIT")
                metadata["image"]["Data Type"] = f"BITPIX={header.get('BITPIX', 'N/A')}"
                metadata["image"]["Equinox"] = header.get("EQUINOX")

                # Processing info
                if header.get("ORIGIN"):
                    metadata["processing"]["Software"] = header.get("ORIGIN")

                # WSClean specific
                if "WSCVERSI" in header:
                    metadata["processing"]["WSClean Version"] = header.get("WSCVERSI")
                if "WSCWEIGH" in header:
                    metadata["processing"]["Weighting"] = header.get("WSCWEIGH")
                # if 'WSCNITER' in header:
                #    metadata['processing']['Iterations'] = f"{int(float(header['WSCNITER']))}"
                # if 'WSCGAIN' in header:
                #    metadata['processing']['Gain'] = f"{float(header['WSCGAIN']):.3f}"

                # History
                history_cards = [str(h) for h in header.get("HISTORY", [])]
                if history_cards:
                    # Combine and summarize history
                    history_text = " ".join(history_cards)
                    if len(history_text) > 500:
                        history_text = history_text[:500] + "..."
                    metadata["processing"]["History"] = history_text

        elif CASA_AVAILABLE:
            # Use CASA tools for CASA images or FITS without astropy
            ia_tool = IA()
            ia_tool.open(imagename)

            try:
                summary = ia_tool.summary(list=False, verbose=True)
                shape = ia_tool.shape()
                csys = ia_tool.coordsys()

                # Parse summary messages to extract useful fields
                # Messages are multi-line strings that need to be split first
                if "messages" in summary:
                    for msg_block in summary["messages"]:
                        # Split the message block into individual lines
                        lines = str(msg_block).split("\n")
                        for line in lines:
                            line = line.strip()
                            if not line or line.startswith("-"):
                                continue

                            # Skip table header lines
                            if (
                                line.startswith("Axis")
                                or line.startswith("0 ")
                                or line.startswith("1 ")
                                or line.startswith("2 ")
                                or line.startswith("3 ")
                            ):
                                continue

                            # Parse key: value pairs
                            if ":" in line:
                                key, _, value = line.partition(":")
                                key = key.strip()
                                value = value.strip()

                                # Skip empty values or very long values or coordinate values
                                if not value or len(value) > 150:
                                    continue

                                # Skip if value looks like a coordinate table entry
                                if value.startswith("[") and "ITRF" in value:
                                    metadata["observation"][
                                        "Telescope Position"
                                    ] = value
                                    metadata["raw_header"][key] = value
                                    continue

                                # Always add to raw_header for "All Headers" view
                                metadata["raw_header"][key] = value

                                # Also map to appropriate organized sections
                                if key in ["Object name", "OBJECT"]:
                                    metadata["observation"]["Object"] = value
                                elif key == "Image name":
                                    # Just the filename, not the full path
                                    metadata["observation"]["Image Name"] = (
                                        value.split("/")[-1] if "/" in value else value
                                    )
                                elif key == "Image type":
                                    metadata["image"]["Image Type"] = value
                                elif key == "Image quantity":
                                    metadata["image"]["Quantity"] = value
                                elif key == "Image units":
                                    metadata["image"]["Units"] = value
                                elif key in [
                                    "Spectral  reference",
                                    "Spectral reference",
                                ]:
                                    metadata["spectral"]["Reference Frame"] = value
                                elif key in ["Velocity  type", "Velocity type"]:
                                    metadata["spectral"]["Velocity Type"] = value
                                elif key in ["Direction reference"]:
                                    metadata["image"]["Direction Reference"] = value
                                elif key == "Telescope":
                                    if not metadata["observation"].get("Telescope"):
                                        metadata["observation"]["Telescope"] = value
                                elif key == "Observer":
                                    if not metadata["observation"].get("Observer"):
                                        metadata["observation"]["Observer"] = value
                                elif key == "Date observation":
                                    if not metadata["observation"].get("Date/Time"):
                                        metadata["observation"]["Date/Time"] = (
                                            format_datetime(
                                                value.replace("/", "-")
                                                .replace("-", "-", 2)
                                                .replace("/", "T", 1)
                                            )
                                        )
                                elif key == "Restoring Beam":
                                    metadata["beam"]["Restoring Beam"] = value
                                elif key in ["Pixel mask(s)", "Region(s)"]:
                                    if value and value != "None":
                                        metadata["processing"][key] = value

                # Image dimensions
                if len(shape) >= 2:
                    metadata["image"]["Dimensions"] = f"{shape[0]} × {shape[1]} pixels"

                # Coordinate info
                try:
                    refval = csys.referencevalue()["numeric"]
                    axis_names = list(csys.names())
                    _is_solar = "SOLAR-X" in axis_names or "SOLAR-Y" in axis_names
                    if len(refval) >= 2:
                        if _is_solar:
                            # SOLAR-X/Y: values are in arcsec
                            ra_deg = refval[0] / 3600.0
                            dec_deg = refval[1] / 3600.0
                        else:
                            ra_deg = refval[0] * 180 / np.pi
                            dec_deg = refval[1] * 180 / np.pi
                        metadata["image"]["Reference RA"] = format_ra_hms(ra_deg)
                        metadata["image"]["Reference Dec"] = format_dec_dms(dec_deg)
                except:
                    pass

                # Pixel scale
                try:
                    increment = csys.increment()["numeric"]
                    if len(increment) >= 2:
                        if _is_solar:
                            cdelt1_arcsec = abs(increment[0])  # already arcsec
                            cdelt2_arcsec = abs(increment[1])
                        else:
                            cdelt1_arcsec = abs(increment[0]) * 180 / np.pi * 3600
                            cdelt2_arcsec = abs(increment[1]) * 180 / np.pi * 3600
                        metadata["image"][
                            "Pixel Scale"
                        ] = f"{cdelt1_arcsec:.3f} × {cdelt2_arcsec:.3f} arcsec/pixel"
                except:
                    pass

                # Frequency
                try:
                    units = csys.units()
                    refval = csys.referencevalue()["numeric"]
                    for i, unit in enumerate(units):
                        if unit == "Hz" and i < len(refval):
                            freq_hz = refval[i]
                            metadata["spectral"]["Frequency"] = format_frequency(
                                freq_hz
                            )
                            break
                except:
                    pass

                # Beam info
                try:
                    beam = ia_tool.restoringbeam()
                    if beam and "major" in beam:
                        major = beam["major"]["value"]
                        minor = beam["minor"]["value"]
                        if beam["major"]["unit"] == "deg":
                            major *= 3600
                            minor *= 3600
                        metadata["beam"]["Major Axis"] = f"{major:.2f} arcsec"
                        metadata["beam"]["Minor Axis"] = f"{minor:.2f} arcsec"
                        if "positionangle" in beam:
                            metadata["beam"][
                                "Position Angle"
                            ] = f"{beam['positionangle']['value']:.1f}°"
                except:
                    pass

                try:
                    bunit = ia_tool.brightnessunit()
                    if bunit:
                        metadata["image"]["Units"] = bunit
                except:
                    pass

                # Extract all keywords from miscinfo and store important ones
                try:
                    miscinfo = ia_tool.miscinfo()

                    # Store miscinfo keys as raw headers (only simple values, not nested structures)
                    for key, value in miscinfo.items():
                        # Skip None, empty, dicts, lists, and overly long values
                        if value is None:
                            continue
                        if isinstance(value, (dict, list, tuple)):
                            continue
                        value_str = str(value).strip()
                        if not value_str or len(value_str) > 200:
                            continue
                        # Skip if value contains newlines (multi-line output)
                        if "\n" in value_str:
                            continue
                        metadata["raw_header"][key] = value_str

                    # Observation section
                    telescope_keys = ["TELESCOP", "INSTRUME", "ANTENNA"]
                    for key in telescope_keys:
                        if key in miscinfo and miscinfo[key]:
                            metadata["observation"]["Telescope"] = miscinfo[key]
                            break

                    datetime_keys = ["DATE-OBS", "DATEOBS", "DATE_OBS", "OBSDATE"]
                    for key in datetime_keys:
                        if key in miscinfo and miscinfo[key]:
                            metadata["observation"]["Date/Time"] = format_datetime(
                                miscinfo[key]
                            )
                            break

                    if "OBSERVER" in miscinfo and miscinfo["OBSERVER"]:
                        metadata["observation"]["Observer"] = miscinfo["OBSERVER"]

                    object_keys = ["OBJECT", "SRCNAME", "TARGET"]
                    for key in object_keys:
                        if key in miscinfo and miscinfo[key]:
                            metadata["observation"]["Object"] = miscinfo[key]
                            break

                    if "ORIGIN" in miscinfo and miscinfo["ORIGIN"]:
                        metadata["observation"]["Origin"] = miscinfo["ORIGIN"]

                    # Processing section - capture software info
                    if "ORIGIN" in miscinfo and miscinfo["ORIGIN"]:
                        metadata["processing"]["Software"] = miscinfo["ORIGIN"]

                    # WSClean specific keywords
                    wsclean_keys = {
                        "WSCVERSI": "WSClean Version",
                        "WSCWEIGH": "Weighting",
                        "WSCNWLAY": "W-Layers",
                        "WSCCHANS": "Channels",
                    }
                    for key, display_name in wsclean_keys.items():
                        if key in miscinfo and miscinfo[key]:
                            metadata["processing"][display_name] = miscinfo[key]

                    # CASA/tclean specific keywords
                    casa_keys = {
                        "IMAGER": "Imager",
                        "IMAGETYP": "Image Type",
                        "PROJECT": "Project",
                    }
                    for key, display_name in casa_keys.items():
                        if key in miscinfo and miscinfo[key]:
                            metadata["processing"][display_name] = miscinfo[key]

                except Exception as e:
                    pass

            finally:
                ia_tool.close()
        else:
            metadata["observation"]["Error"] = "No FITS/CASA tools available"

    except Exception as e:
        metadata["observation"]["Error"] = f"Failed to read metadata: {str(e)}"

    # Clean up None values and empty sections
    for section in list(metadata.keys()):
        if section == "raw_header":
            continue
        metadata[section] = {
            k: v for k, v in metadata[section].items() if v is not None
        }
        if not metadata[section]:
            del metadata[section]

    return metadata


def format_metadata_text(metadata):
    """
    Format metadata dictionary as readable text.

    Parameters:
        metadata: Dictionary from get_image_metadata()

    Returns:
        Formatted text string
    """
    lines = []

    section_titles = {
        "observation": "📅 Observation",
        "spectral": "📡 Spectral Properties",
        "beam": "🎯 Beam Properties",
        "image": "🖼️ Image Properties",
        "processing": "⚙️ Processing",
    }

    for section, title in section_titles.items():
        if section in metadata and metadata[section]:
            lines.append(f"\n{title}")
            lines.append("─" * 40)
            for key, value in metadata[section].items():
                # Handle long values
                if len(str(value)) > 60:
                    lines.append(f"  {key}:")
                    lines.append(f"    {value}")
                else:
                    lines.append(f"  {key}: {value}")

    return "\n".join(lines)
