import execnet
import matplotlib 
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import sys, os, numpy as np
from scipy.signal import medfilt
from scipy import interpolate
import matplotlib.dates as md
from optparse import OptionParser
from matplotlib.patches import Rectangle
from collections import OrderedDict
import subprocess
import calendar

def call_python_version(Version, Module, Function, ArgumentList):
    """
    Function to run code using a different Python version.
    (If you no longer need to call Python 2, you can remove or update this.)
    """
    gw = execnet.makegateway("popen//python=python%s" % Version)
    channel = gw.remote_exec("""
from %s import %s as the_function
channel.send(the_function(*channel.receive()))
""" % (Module, Function))
    channel.send(ArgumentList)
    return channel.receive()

def fill_nan(arr):
    """
    Interpolate to fill nan values.
    """
    try:
        med_fill_value = np.nanmedian(arr)
        inds = np.arange(arr.shape[0])
        good = np.where(np.isfinite(arr))
        f = interpolate.interp1d(inds[good], arr[good],
                                 bounds_error=False, kind='linear', fill_value='extrapolate')
        out_arr = np.where(np.isfinite(arr), arr, f(inds))
    except Exception as e:
        print(e)
        out_arr = arr
    return out_arr

def backsub(data):
    """
    Subtract background per channel.
    """
    for sb in range(data.shape[0]):
        data[sb, :] = data[sb, :] / np.nanmedian(data[sb, :])
    return data

def srs_to_pd(srs_file, pd_file, bkg_sub=False, do_flag=True, flag_cal_time=True):
    """
    Convert Learmonth SRS datafile into a pandas dataframe.
    """
    print('Converting SRS file to pandas datafile...\n')
    # Use subprocess to run srs_data.py and capture its output.
    try:
        raw_output = subprocess.check_output(['python', 'srs_data.py', srs_file],
                                             universal_newlines=True)
        # It is assumed that srs_data.py outputs a Python literal (e.g., a tuple) that we can evaluate.
        raw_data = eval(raw_output)
    except Exception as e:
        print("Error running srs_data.py:", e)
        return None

    a_band_data = raw_data[0]  # 25 to 75 MHz
    b_band_data = raw_data[1]  # 75 to 180 MHz
    timestamps = raw_data[2]
    timestamps = pd.to_datetime(timestamps, format='%d/%m/%y, %H:%M:%S')
    a_band_freqs = list(a_band_data[0].keys())
    b_band_freqs = list(b_band_data[0].keys())
    freqs = a_band_freqs + b_band_freqs
    freqs = np.array(freqs)
    freqs = np.round(freqs, 1)
    x = []
    for i in range(len(a_band_data)):
        a_data = list(a_band_data[i].values())
        b_data = list(b_band_data[i].values())
        a_b_data = a_data + b_data
        x.append(a_b_data)
    x = np.array(x).astype('float')
    full_band_data = pd.DataFrame(x, index=timestamps, columns=freqs)
    full_band_data = full_band_data.sort_index(axis=0)
    full_band_data = full_band_data.sort_index(axis=1)
    freqs = full_band_data.index
    final_data = full_band_data.to_numpy().astype('float')
    
    # Flag bad channels if required
    if do_flag:
        final_data[488:499, :] = np.nan
        final_data[524:533, :] = np.nan
        final_data[540:550, :] = np.nan
        final_data[638:642, :] = np.nan
        final_data[119:129, :] = np.nan
        final_data[108:111, :] = np.nan
        final_data[150:160, :] = np.nan
        final_data[197:199, :] = np.nan
        final_data[285:289, :] = np.nan
        final_data[621:632, :] = np.nan
        final_data[592:600, :] = np.nan
        final_data[700:712, :] = np.nan
        final_data[410:416, :] = np.nan
        final_data[730:741, :] = np.nan
        final_data[635:645, :] = np.nan
        final_data[283:292, :] = np.nan
        final_data[216:222, :] = np.nan
        final_data[590:602, :] = np.nan
        final_data[663:667, :] = np.nan
        final_data[684:690, :] = np.nan
        final_data[63:66, :] = np.nan
        final_data[54:59, :] = np.nan
        final_data[27:31, :] = np.nan

        # Flag calibration times if requested.
        if flag_cal_time:
            y = np.nanmedian(final_data, axis=0)
            c = y / medfilt(y, 1001)
            c_std = np.nanstd(c)
            pos = np.where(c > 1 + (10 * c_std))
            final_data[..., pos] = np.nan

    # Interpolate over NaNs for each time slice.
    for i in range(final_data.shape[1]):
        final_data[:, i] = fill_nan(final_data[:, i])
    if do_flag:
        final_data[780:, :] = np.nan  # Flag edge channels
    if bkg_sub:
        final_data = backsub(final_data)
    
    full_band_data = pd.DataFrame(final_data, index=freqs, columns=timestamps)
    full_band_data.to_pickle(pd_file + '.pd')
    return pd_file + '.pd'

def plot_learmonth_DS(pd_file, save_file='', start_time='', end_time=''):
    """
    Plot the dynamic spectrum.
    """
    print('Making final dynamic spectrum\n')
    if save_file == '':
        save_file = pd_file.split('.pd')[0] + '.pdf'
    pd_data = pd.read_pickle(pd_file)
    
    if start_time:
        start_time = pd.to_datetime(start_time)
    if end_time:
        end_time = pd.to_datetime(end_time)
    
    timestamps = pd_data.columns
    freqs = pd_data.index
    if start_time and end_time:
        pos = ((timestamps >= start_time) & (timestamps <= end_time))
        sel_timestamps = timestamps[pos]
    elif start_time and not end_time:
        pos = (timestamps >= start_time)
        sel_timestamps = timestamps[pos]
    elif not start_time and end_time:
        pos = (timestamps <= end_time)
        sel_timestamps = timestamps[pos]
    else:
        sel_timestamps = timestamps
    sel_data = pd_data[sel_timestamps]
    
    matplotlib.rcParams.update({'font.size': 15})
    # Set tick indices ensuring step is at least 1
    step_time = max(1, int(len(sel_timestamps) / 10))
    time_ind = list(range(0, len(sel_timestamps), step_time))
    time_list = [sel_timestamps[i].time() for i in time_ind]
    step_freq = max(1, int(len(freqs) / 10))
    freq_ind = list(range(0, len(freqs), step_freq))
    freq_list = [freqs[i] for i in freq_ind]
    
    fig, ax = plt.subplots(figsize=(12, 8))
    s = sns.heatmap(sel_data, robust=True, cbar_kws={'label': 'Flux density (arbitrary unit)'}, rasterized=True)
    s.invert_yaxis()
    plt.yticks(freq_ind, freq_list)
    plt.xticks(time_ind, time_list, rotation=30)
    plt.xlabel('Timestamp (UTC)')
    plt.ylabel('Frequency (MHz)')
    
    # Use calendar to get month name from the timestamp in the middle.
    t = timestamps[int(len(timestamps) / 2)]
    plt.title('Learmonth spectrograph dynamic spectrum\nDate : ' +
              str(t.day) + ' ' + calendar.month_name[t.month] + ' ' + str(t.year))
    plt.tight_layout()
    plt.savefig(save_file)
    plt.show()
    return save_file

def download_learmonth(start_time='', end_time=''):
    """
    Download Learmonth spectrograph data.
    """
    if not start_time:
        print('Please provide start time.\n')
        return
    if not end_time:
        print('Please provide end time.\n')
        return
    start_time = pd.to_datetime(start_time)
    end_time = pd.to_datetime(end_time)
    datestamp = start_time.date()
    year_stamp = str(datestamp.year)[2:]
    month_stamp = f"{datestamp.month:02d}"
    day_stamp = f"{datestamp.day:02d}"
    file_name = 'LM' + year_stamp + month_stamp + day_stamp + '.srs'
    if not os.path.exists(file_name):
        print('Downloading data....\n')
        download_link = f'https://downloads.sws.bom.gov.au/wdc/wdc_spec/data/learmonth/raw/{year_stamp}/{file_name}'
        os.system('wget ' + download_link)
    return file_name

def main():
    usage = 'Download and plot dynamic spectrum from Learmonth Solar Radiograph'
    parser = OptionParser(usage=usage)
    parser.add_option('--starttime', dest="start_time", default=None,
                      help="Start time of the dynamic spectrum (format: dd-mm-yyyy hh:mm:ss)",
                      metavar="Datetime String")
    parser.add_option('--endtime', dest="end_time", default=None,
                      help="End time of the dynamic spectrum (format: dd-mm-yyyy hh:mm:ss)",
                      metavar="Datetime String")
    parser.add_option('--background_subtract', dest="bkg_sub", default=False,
                      help="Perform background subtraction", metavar="Boolean")
    parser.add_option('--flag', dest="flag", default=True,
                      help="Perform flagging", metavar="Boolean")
    parser.add_option('--flag_caltime', dest="flag_caltime", default=True,
                      help="Perform cal time flagging", metavar="Boolean")
    parser.add_option('--overwrite', dest="overwrite", default=False,
                      help="Overwrite existing pd file", metavar="Boolean")
    parser.add_option('--plot_format', dest="ext", default='pdf',
                      help="Final dynamic spectrum format (pdf,png,jpg,eps)",
                      metavar="String")
    (options, args) = parser.parse_args()
    
    srs_file = download_learmonth(start_time=options.start_time, end_time=options.end_time)
    print('Downloaded SRS file : ' + srs_file + '\n')
    pd_file = srs_file.split('.srs')[0]
    if not os.path.exists(pd_file + '.pd') or eval(str(options.overwrite)) == True:
        pd_file = srs_to_pd(srs_file, pd_file,
                            bkg_sub=eval(str(options.bkg_sub)),
                            do_flag=eval(str(options.flag)),
                            flag_cal_time=eval(str(options.flag_caltime)))
    else:
        pd_file = pd_file + '.pd'
    print('Pandas datafile : ' + pd_file + '\n')
    save_file = srs_file.split('.srs')[0] + '.' + str(options.ext)
    final_plot = plot_learmonth_DS(pd_file, save_file=save_file,
                                   start_time=options.start_time,
                                   end_time=options.end_time)
    print('Dynamic spectrum saved at : ' + final_plot + '\n')

if __name__ == '__main__':
    main()

