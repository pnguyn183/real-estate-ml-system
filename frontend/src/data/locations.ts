export interface DistrictOption {
  label: string;
  value: string;
}

export interface ProvinceOption {
  label: string;
  value: string;
  districts: DistrictOption[];
}

export const PROVINCES: ProvinceOption[] = [
  {
    label: 'Ha Noi',
    value: 'ha-noi',
    districts: [
      { label: 'Ba Dinh', value: 'ba-dinh' },
      { label: 'Cau Giay', value: 'cau-giay' },
      { label: 'Dong Da', value: 'dong-da' },
      { label: 'Ha Dong', value: 'ha-dong' },
      { label: 'Hai Ba Trung', value: 'hai-ba-trung' },
      { label: 'Hoan Kiem', value: 'hoan-kiem' },
      { label: 'Hoang Mai', value: 'hoang-mai' },
      { label: 'Long Bien', value: 'long-bien' },
      { label: 'Nam Tu Liem', value: 'nam-tu-liem' },
      { label: 'Tay Ho', value: 'tay-ho' },
      { label: 'Thanh Xuan', value: 'thanh-xuan' },
    ],
  },
  {
    label: 'Ho Chi Minh City',
    value: 'ho-chi-minh',
    districts: [
      { label: 'District 1', value: 'quan-1' },
      { label: 'District 3', value: 'quan-3' },
      { label: 'District 7', value: 'quan-7' },
      { label: 'Binh Thanh', value: 'binh-thanh' },
      { label: 'Binh Tan', value: 'binh-tan' },
      { label: 'Go Vap', value: 'go-vap' },
      { label: 'Phu Nhuan', value: 'phu-nhuan' },
      { label: 'Tan Binh', value: 'tan-binh' },
      { label: 'Tan Phu', value: 'tan-phu' },
      { label: 'Thu Duc', value: 'thu-duc' },
    ],
  },
  {
    label: 'Da Nang',
    value: 'da-nang',
    districts: [
      { label: 'Cam Le', value: 'cam-le' },
      { label: 'Hai Chau', value: 'hai-chau' },
      { label: 'Lien Chieu', value: 'lien-chieu' },
      { label: 'Ngu Hanh Son', value: 'ngu-hanh-son' },
      { label: 'Son Tra', value: 'son-tra' },
      { label: 'Thanh Khe', value: 'thanh-khe' },
    ],
  },
  {
    label: 'Binh Duong',
    value: 'binh-duong',
    districts: [
      { label: 'Ben Cat', value: 'ben-cat' },
      { label: 'Di An', value: 'di-an' },
      { label: 'Tan Uyen', value: 'tan-uyen' },
      { label: 'Thu Dau Mot', value: 'thu-dau-mot' },
      { label: 'Thuan An', value: 'thuan-an' },
    ],
  },
  {
    label: 'Dong Nai',
    value: 'dong-nai',
    districts: [
      { label: 'Bien Hoa', value: 'bien-hoa' },
      { label: 'Long Thanh', value: 'long-thanh' },
      { label: 'Nhon Trach', value: 'nhon-trach' },
      { label: 'Trang Bom', value: 'trang-bom' },
    ],
  },
  {
    label: 'Khanh Hoa',
    value: 'khanh-hoa',
    districts: [
      { label: 'Cam Ranh', value: 'cam-ranh' },
      { label: 'Dien Khanh', value: 'dien-khanh' },
      { label: 'Nha Trang', value: 'nha-trang' },
    ],
  },
  {
    label: 'Ba Ria - Vung Tau',
    value: 'ba-ria-vung-tau',
    districts: [
      { label: 'Ba Ria', value: 'ba-ria' },
      { label: 'Long Dien', value: 'long-dien' },
      { label: 'Phu My', value: 'phu-my' },
      { label: 'Vung Tau', value: 'vung-tau' },
    ],
  },
];

export function getDistrictsByProvince(provinceSlug?: string): DistrictOption[] {
  return PROVINCES.find((province) => province.value === provinceSlug)?.districts || [];
}
