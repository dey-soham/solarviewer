from struct import unpack
import numpy as np
import sys

# constants from the file spec
RECORD_SIZE = 826
RECORD_HEADER_SIZE = 24
RECORD_ARRAY_SIZE = 401

# verbosity values
VERBOSITY_ALL = 2  # print warnings and errors
VERBOSITY_ERRORS = 1  # print errors
VERBOSITY_NONE = 0  # print nothing


class SRSRecord:
    """Holds one 826 byte SRS Record."""

    _site_to_name = {
        1: "Palehua",
        2: "Holloman",
        3: "Learmonth",
        4: "San Vito",
        # add new site names here ..
    }

    def __init__(self):
        self.year = None
        self.month = None
        self.day = None
        self.hour = None
        self.minute = None
        self.seconds = None

        self.site_number = None
        self.site_name = None
        self.n_bands_per_record = None

        self.a_start_freq = None
        self.a_end_freq = None
        self.a_num_bytes = None
        self.a_analyser_reference_level = None
        self.a_analyser_attenuation = None

        self.b_start_freq = None
        self.b_end_freq = None
        self.b_num_bytes = None
        self.b_analyser_reference_level = None
        self.b_analyser_attenuation = None

        # dictionary that maps frequency in MHz to level
        self.a_values = {}

        # dictionary that maps frequency in MHz to level
        self.b_values = {}

    def _parse_srs_file_header(self, header_bytes, verbosity=VERBOSITY_ALL):
        fields = unpack(
            ">"  # big endian format
            "B"  # Year (last 2 digits)
            "B"  # Month
            "B"  # Day
            "B"  # Hour (UT)
            "B"  # Minute
            "B"  # Second
            "B"  # Site Number
            "B"  # Number of bands (should be 2)
            "h"  # A-band start frequency (MHz)
            "H"  # A-band end frequency (MHz)
            "H"  # A-band number of bytes (should be 401)
            "B"  # A-band analyser reference level
            "B"  # A-band analyser attenuation (dB)
            "H"  # B-band start frequency (MHz)
            "H"  # B-band end frequency (MHz)
            "H"  # B-band number of bytes (should be 401)
            "B"  # B-band analyser reference level
            "B",  # B-band analyser attenuation (dB)
            header_bytes,
        )

        self.year = fields[0]
        self.month = fields[1]
        self.day = fields[2]
        self.hour = fields[3]
        self.minute = fields[4]
        self.seconds = fields[5]

        self.site_number = fields[6]
        if self.site_number not in list(SRSRecord._site_to_name.keys()):
            if verbosity >= VERBOSITY_ALL:
                print("Unknown site number: %s" % self.site_number)
                print("A list of known site numbers follows:")
                for site_number, site_name in SRSRecord._site_to_name.items():
                    print("\t%s: %s" % (site_number, site_name))
            self.site_name = "UnknownSite"
        else:
            self.site_name = SRSRecord._site_to_name[self.site_number]

        self.n_bands_per_record = fields[7]
        if self.n_bands_per_record != 2 and verbosity >= VERBOSITY_ERRORS:
            print(
                "Warning.. record has %s bands, expecting 2!" % self.n_bands_per_record
            )

        self.a_start_freq = fields[8]
        self.a_end_freq = fields[9]
        self.a_num_bytes = fields[10]
        if self.a_num_bytes != 401 and verbosity >= VERBOSITY_ERRORS:
            print(
                "Warning.. record has %s bytes in the a array, expecting 401!"
                % self.a_num_bytes
            )

        self.a_analyser_reference_level = fields[11]
        self.a_analyser_attenuation = fields[12]

        self.b_start_freq = fields[13]
        self.b_end_freq = fields[14]
        self.b_num_bytes = fields[15]
        if self.b_num_bytes != 401 and verbosity >= VERBOSITY_ERRORS:
            print(
                "Warning.. record has %s bytes in the b array, expecting 401!"
                % self.b_num_bytes
            )

        self.b_analyser_reference_level = fields[16]
        self.b_analyser_attenuation = fields[17]

    def _parse_srs_a_levels(self, a_bytes):
        # Unpack the frequency/levels from the first array.
        for i in range(401):
            # Calculate the frequency in MHz
            freq_a = 25 + 50 * i / 400.0
            # Use slicing (a_bytes[i:i+1]) to obtain a one-byte bytes object.
            level_a = unpack(">B", a_bytes[i : i + 1])[0]
            self.a_values[freq_a] = level_a
        return

    def _parse_srs_b_levels(self, b_bytes):
        for i in range(401):
            freq_b = 75 + 105 * i / 400.0
            level_b = unpack(">B", b_bytes[i : i + 1])[0]
            self.b_values[freq_b] = level_b
        return

    def __str__(self):
        # Return a formatted string representation of the timestamp.
        return f"{self.day:02d}/{self.month:02d}/{self.year:02d}, {self.hour:02d}:{self.minute:02d}:{self.seconds:02d}"

    def _dump(self, values):
        freqs = list(values.keys())
        freqs.sort()
        return values

    def dump_a(self):
        return self._dump(self.a_values)

    def dump_b(self):
        return self._dump(self.b_values)


def read_srs_file(fname):
    """Parses an SRS file and returns a list of SRSRecord objects."""
    srs_records = []
    with open(fname, "rb") as f:
        while True:
            record_data = f.read(RECORD_SIZE)
            if len(record_data) == 0:
                break
            header_bytes = record_data[:RECORD_HEADER_SIZE]
            a_bytes = record_data[
                RECORD_HEADER_SIZE : RECORD_HEADER_SIZE + RECORD_ARRAY_SIZE
            ]
            b_bytes = record_data[
                RECORD_HEADER_SIZE
                + RECORD_ARRAY_SIZE : RECORD_HEADER_SIZE
                + 2 * RECORD_ARRAY_SIZE
            ]
            record = SRSRecord()
            record._parse_srs_file_header(header_bytes, verbosity=VERBOSITY_ERRORS)
            record._parse_srs_a_levels(a_bytes)
            record._parse_srs_b_levels(b_bytes)
            srs_records.append(record)
    return srs_records


def main(srs_file):
    srs_records = read_srs_file(fname=srs_file)
    # Create timestamps from each record's string representation.
    timestamps = [str(record) for record in srs_records]
    final_data_a = []
    final_data_b = []
    for record in srs_records:
        final_data_a.append(record.dump_a())
        final_data_b.append(record.dump_b())
    final_data = [final_data_a, final_data_b, timestamps]
    return final_data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python srs.py <srs_file>")
    else:
        srs_file = sys.argv[1]
        result = main(srs_file)
        print(result)
