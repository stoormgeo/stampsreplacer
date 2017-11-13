from datetime import datetime
import numpy as np
import numpy.matlib
import sys
import math
from pathlib import Path

from scripts.MetaSubProcess import MetaSubProcess
from scripts.processes.PsEstGamma import PsEstGamma
from scripts.processes.PsFiles import PsFiles
from scripts.processes.PsSelect import PsSelect
from scripts.utils.ArrayUtils import ArrayUtils
from scripts.utils.FolderConstants import FolderConstants
from scripts.utils.LoggerFactory import LoggerFactory
from scripts.utils.MatlabUtils import MatlabUtils


class PsWeed(MetaSubProcess):
    """Pikslite filtreerimine teiste naabrusest. Valitakse hulgast vaid selgemad"""

    __IND_ARRAY_TYPE = np.int32
    __DEF_NEIGHBOUR_VAL = -1

    def __init__(self, path_to_patch: str, ps_files: PsFiles, ps_est_gamma: PsEstGamma,
                 ps_select: PsSelect):
        self.ps_files = ps_files
        self.ps_select = ps_select
        self.ps_est_gamma = ps_est_gamma

        self.__logger = LoggerFactory.create("PsWeed")

        self.__time_win = 730
        self.__weed_standard_dev = 1
        self.__weed_max_noise = sys.maxsize  # Stampsis oli tavaväärtus inf
        self.__weed_zero_elevation = False
        self.__weed_neighbours = True
        # todo drop_ifg_index on juba PsSelect'is
        self.__drop_ifg_index = np.array([])
        self.__small_baseline = True

        #todo object? tuple?
        #todo milleks üldse ps_weed_edge_nr? see on ju len(ps_weed_edge_data)
        ps_weed_edge_nr, ps_weed_edge_data = self.__load_psweed_edge_file(path_to_patch)

    def __load_psweed_edge_file(self, path: str) -> (int, np.ndarray):
        """Põhjus miks me ei loe seda faili sisse juba PsFiles'ides on see, et me ei pruugi
        PsWeed protsessi jõuda enda töötluses ja seda läheb ainult siin vaja"""

        file_name = "psweed.2.edge"
        path = Path(path, FolderConstants.PATCH_FOLDER_NAME, file_name)
        self.__logger.debug("Path to psweed_edgke file: " + str(path))
        if path.exists():
            header = np.genfromtxt(path, max_rows=1, dtype=self.__IND_ARRAY_TYPE)
            data = np.genfromtxt(path, skip_header=True, skip_footer=True, dtype=self.__IND_ARRAY_TYPE)
            return header[0], data
        else:
            raise FileNotFoundError("{1} not found. AbsPath {0}".format(str(path.absolute()), file_name))



    class __DataDTO(object):

        def __init__(self, ind: np.ndarray, ph_res: np.ndarray, coh_thresh_ind: np.ndarray,
                     k_ps: np.ndarray, c_ps: np.ndarray, coh_ps: np.ndarray, pscands_ij: np.matrix,
                     xy: np.ndarray, lonlat: np.matrix, hgt: np.ndarray, ph: np.ndarray,
                     ph2: np.ndarray, ph_patch_org: np.ndarray, bperp: np.ndarray, nr_ifgs: int,
                     nr_ps: int, master_date: datetime):
            self.ind = ind
            self.ph_res = ph_res
            self.coh_thresh_ind = coh_thresh_ind
            self.k_ps = k_ps
            self.c_ps = c_ps
            self.coh_ps = coh_ps
            self.pscands_ij = pscands_ij
            self.xy = xy
            self.lonlat = lonlat
            self.hgt = hgt
            self.ph_patch_org = ph_patch_org
            self.ph = ph
            self.ph2 = ph2
            self.bperp = bperp
            self.nr_ifgs = nr_ifgs
            self.nr_ps = nr_ps
            self.master_date = master_date

    def start_process(self):
        self.__logger.info("Start")

        data = self.__load_ps_params()
        # Stamps*is oli see nimetatud kui nr_ps, aga see on meil juba olemas
        coh_thresh_ind_len = len(data.coh_thresh_ind)
        self.__logger.debug("Loaded data. coh_thresh_ind.len: {0}, coh_thresh_ind_len: {1}"
                            .format(coh_thresh_ind_len, data.nr_ps))

        ij_shift = self.__get_ij_shift(data.pscands_ij, coh_thresh_ind_len)
        self.__logger.debug("ij_shift.len: {0}".format(len(ij_shift)))

        neighbour_ind = self.__init_neighbours(ij_shift, coh_thresh_ind_len)
        self.__logger.debug("neighbours.len: {0}".format(len(neighbour_ind)))

        neighbour_ps = self.__find_neighbours(ij_shift, coh_thresh_ind_len, neighbour_ind)
        # todo kas saab logida ka tühjade arvu?
        self.__logger.debug("neighbour_ps.len: {0}".format(len(neighbour_ps)))

        selectable_ps = self.__select_best(neighbour_ps, coh_thresh_ind_len, data.coh_ps, data.hgt)
        self.__logger.debug("selectable_ps.len: {0}, true vals: {1}"
                            .format(len(selectable_ps), np.count_nonzero(selectable_ps)))
        # todo del neighbour_ps?

        xy, selectable_ps = self.__filter_xy(data.xy, selectable_ps, data.coh_ps)

        # Stamps'is oli selle asemel 'no_weed_noisy'
        if not (self.__weed_standard_dev >= math.pi and self.__weed_max_noise >= math.pi):
            self.__drop_noisy()

        self.__logger.info("End")

    def __load_ps_params(self):

        def get_from_ps_select():
            ind = self.ps_select.keep_ind
            ph_res = self.ps_select.ph_res[ind]

            if len(ind) > 0:
                coh_thresh_ind = self.ps_select.coh_thresh_ind[ind]
                c_ps = self.ps_select.c_ps[ind]
                k_ps = self.ps_select.k_ps[ind]
                coh_ps = self.ps_select.coh_ps2[ind]
            else:
                coh_thresh_ind = self.ps_select.coh_thresh_ind
                c_ps = self.ps_select.c_ps
                k_ps = self.ps_select.k_ps
                coh_ps = self.ps_select.coh_ps2

            return ind, ph_res, coh_thresh_ind, k_ps, c_ps, coh_ps

        def get_from_ps_files():
            pscands_ij = self.ps_files.pscands_ij[coh_thresh_ind]
            xy = self.ps_files.xy[coh_thresh_ind]
            ph = self.ps_files.ph[coh_thresh_ind]
            lonlat = self.ps_files.lonlat[coh_thresh_ind]
            hgt = self.ps_files.hgt[coh_thresh_ind]

            return pscands_ij, xy, ph, lonlat, hgt

        def get_from_ps_est_gamma():

            ph_patch_org = self.ps_est_gamma.ph_patch[coh_thresh_ind, :]
            ph, bperp, nr_ifgs, nr_ps, _, _ = self.ps_files.get_ps_variables()
            master_date = self.ps_files.master_date

            return ph_patch_org, ph, bperp, nr_ifgs, nr_ps, master_date

        # fixme ph_path'e on Stampsis ainult üks.

        ind, ph_res, coh_thresh_ind, k_ps, c_ps, coh_ps = get_from_ps_select()

        pscands_ij, xy, ph2, lonlat, hgt = get_from_ps_files()

        ph_patch_org, ph, bperp, nr_ifgs, nr_ps, master_date = get_from_ps_est_gamma()

        # Stamps'is oli siin oli ka lisaks 'all_da_flag' ja leiti teised väärtused muutujatele k_ps,
        # c_ps, coh_ps, ph_patch_org, ph_res

        return self.__DataDTO(ind, ph_res, coh_thresh_ind, k_ps, c_ps, coh_ps, pscands_ij, xy,
                              lonlat, hgt, ph, ph2, ph_patch_org, bperp, nr_ifgs, nr_ps,
                              master_date)

    def __get_ij_shift(self, pscands_ij: np.matrix, coh_ps_len: int) -> np.ndarray:
        ij = np.asarray(pscands_ij[:, 1:3])
        repmated = np.matlib.repmat(np.array([2, 2]) - ij.min(axis=0), coh_ps_len, 1)
        ij_shift = ij + repmated

        return ij_shift

    def __init_neighbours(self, ij_shift: np.ndarray, coh_ps_len: int) -> np.ndarray:
        """Stamps'is täideti massiiv nullidega siis mina täidan siin -1 'ega.
        Kuna täidetakse massiiv indeksitest ja Numpy's/ Python'is hakkavad indeksid nullist siis
        täidame -1'ega ja siis uute väärtustega"""

        def arange_neighbours_select_arr(i, ind):
            return ArrayUtils.arange_include_last(ij_shift[i, ind] - 2, ij_shift[i, ind])

        def make_miss_middle_mask():
            miss_middle = np.ones((3, 3), dtype=bool)
            miss_middle[1, 1] = False

            return miss_middle

        neighbour_ind = np.ones((MatlabUtils.max(ij_shift[:, 0]) + 1,
                                 MatlabUtils.max(ij_shift[:, 1]) + 1),
                                self.__IND_ARRAY_TYPE) * self.__DEF_NEIGHBOUR_VAL
        miss_middle = make_miss_middle_mask()

        for i in range(coh_ps_len):
            start = arange_neighbours_select_arr(i, 0)
            end = arange_neighbours_select_arr(i, 1)

            # Selleks, et saada len(start) * len(end) massiivi tuleb numpy's sedasi selekteerida
            # Võib kasutada ka neighbour_ind[start, :][:, end], aga see ei luba pärast sama moodi
            # väärtustada
            neighbours_val = neighbour_ind[np.ix_(start, end)]
            neighbours_val[(neighbours_val == self.__DEF_NEIGHBOUR_VAL) & (miss_middle == True)] = i

            neighbour_ind[np.ix_(start, end)] = neighbours_val

        return neighbour_ind

    def __find_neighbours(self, ij_shift: np.ndarray, coh_thresh_ind_len: int,
                          neighbour_ind: np.ndarray) -> np.ndarray:
        # Loome tühja listi, kus on sees tühjad numpy massivid
        neighbour_ps = [np.array([], self.__IND_ARRAY_TYPE)] * (coh_thresh_ind_len + 1)
        for i in range(coh_thresh_ind_len):
            ind = neighbour_ind[ij_shift[i, 0] - 1, ij_shift[i, 1] - 1]
            if ind != self.__DEF_NEIGHBOUR_VAL:
                neighbour_ps[ind] = np.append(neighbour_ps[ind], [i])

        return np.array(neighbour_ps)

    def __select_best(self, neighbour_ps: np.ndarray, coh_thresh_ind_len: int,
                      coh_ps: np.ndarray, htg: np.ndarray) -> np.ndarray:
        """Tagastab boolean'idest array, et pärast selle järgi filteerida ülejäänud massiivid.
        Stamps'is oli tegemist massiiv int'intidest"""
        selectable_ps = np.ones(coh_thresh_ind_len, dtype=bool)  # Stamps'is oli see 'ix_weed'

        for i in range(coh_thresh_ind_len):
            ps_ind = neighbour_ps[i]
            if len(ps_ind) != 0:
                j = 0
                while j < len(ps_ind):
                    ps_i = ps_ind[j]
                    ps_ind = np.append(ps_ind, neighbour_ps[ps_i]).astype(self.__IND_ARRAY_TYPE)
                    neighbour_ps[ps_i] = np.array([]) # todo jätaks selle äkki ära? pole mõtet muuta kui pärast neid andmeid ei kasuta
                    j += 1

                ps_ind = np.unique(ps_ind)
                highest_coh_ind = coh_ps[ps_ind].argmax()

                low_coh_ind = np.ones(len(ps_ind), dtype=bool)
                low_coh_ind[highest_coh_ind] = False

                ps_ind = ps_ind[low_coh_ind]
                selectable_ps[ps_ind] = False

        self.__logger.debug("self.__weed_zero_elevation: {0}, len(htg)")
        if self.__weed_zero_elevation and len(htg) > 0:
            self.__logger.debug("Fiding sea evel")
            sea_ind = htg < 1e-6
            selectable_ps[sea_ind] = False

        return selectable_ps

    def __filter_xy(self, xy: np.ndarray, selectable_ps: np.ndarray, coh_ps: np.ndarray):
        """Leiame xy massiiv filteeritult
        Siin oli veel lisaks kas tehtud dublikaatide massiv on tühi, aga selle peale leti weeded_xy
        uuesti, aga mina sellisel tegevusel mõtet ei näinud"""

        #todo funksioon väiksemaks? eraldi xy ja eraldi weed_ind?
        weeded_xy = xy[selectable_ps] # Stamps'is oli see 'xy_weed'

        weed_ind = np.nonzero(selectable_ps)[0] # Stamsp*is oli see 'ix_weed_num' #todo iteratalbe get array?!??
        unique_rows = np.unique(weeded_xy, return_index=True, axis=0)[1].astype(self.__IND_ARRAY_TYPE)
        # Stamps'is transponeeriti ka veel seda järgmist, aga siin ei tee see midagi
        last = np.arange(0, len(weed_ind))
        # Stamps'is oli see 'dps'. Pikslid topelt lon/ lat'iga
        dublicates = np.setxor1d(unique_rows, last)

        for i in range(len(dublicates)):
            dublicate = dublicates[i]
            weeded_dublicates_ind = np.where((weeded_xy[:, 0] == weeded_xy[dublicate, 0]) &
                                      ((weeded_xy[:, 1]) == weeded_xy[dublicate, 1])) # 'dups_ix_weed' oli originaalis
            dublicates_ind = weed_ind[weeded_dublicates_ind] #
            high_coh_ind = coh_ps[dublicates_ind].argmax()
            selectable_ps[dublicates_ind != high_coh_ind] = False

        return xy, selectable_ps

    def __drop_noisy(self):
        pass
