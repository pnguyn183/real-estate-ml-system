import { FormEvent, useState } from 'react';
import { Calculator } from 'lucide-react';
import { PropertyFeatures } from '../api/client';

interface PredictionFormProps {
  onSubmit: (features: PropertyFeatures) => void;
  loading: boolean;
}

// Vietnamese cities and their districts
const PROVINCES: Record<string, { name: string; districts: { value: string; label: string }[] }> = {
  'hanoi': {
    name: 'Hà Nội',
    districts: [
      { value: 'dongda', label: 'Đống Đa' },
      { value: 'hoangmai', label: 'Hoàng Mai' },
      { value: 'thanhtri', label: 'Thanh Trì' },
      { value: 'haibatrung', label: 'Hai Bà Trưng' },
      { value: 'longbien', label: 'Long Biên' },
      { value: 'namtuliem', label: 'Nam Từ Liêm' },
      { value: 'tayho', label: 'Tây Hồ' },
      { value: 'caugiay', label: 'Cầu Giấy' },
      { value: 'gialam', label: 'Gia Lâm' },
      { value: 'hoankiem', label: 'Hoàn Kiếm' },
      { value: 'hadong', label: 'Hà Đông' },
      { value: 'thanhxuan', label: 'Thanh Xuân' },
      { value: 'bacTuLiem', label: 'Bắc Từ Liêm' },
      { value: 'baDinh', label: 'Ba Đình' },
    ],
  },
  'hcm': {
    name: 'TP Hồ Chí Minh',
    districts: [
      { value: 'quan1', label: 'Quận 1' },
      { value: 'quan2', label: 'Quận 2' },
      { value: 'quan3', label: 'Quận 3' },
      { value: 'quan4', label: 'Quận 4' },
      { value: 'quan5', label: 'Quận 5' },
      { value: 'quan6', label: 'Quận 6' },
      { value: 'quan7', label: 'Quận 7' },
      { value: 'quan8', label: 'Quận 8' },
      { value: 'quan9', label: 'Quận 9' },
      { value: 'quan10', label: 'Quận 10' },
      { value: 'quan11', label: 'Quận 11' },
      { value: 'quan12', label: 'Quận 12' },
      { value: 'binhtan', label: 'Bình Tân' },
      { value: 'binhthanh', label: 'Bình Thạnh' },
      { value: 'govap', label: 'Gò Vấp' },
      { value: 'phunhuan', label: 'Phú Nhuận' },
      { value: 'tandinh', label: 'Tân Định' },
      { value: 'thuduc', label: 'Thủ Đức' },
      { value: 'nhabe', label: 'Nhà Bè' },
      { value: 'hocmon', label: 'Hóc Môn' },
      { value: 'cuuchi', label: 'Củ Chi' },
    ],
  },
  'danang': {
    name: 'Đà Nẵng',
    districts: [
      { value: 'haichau', label: 'Hải Châu' },
      { value: 'thanhkhe', label: 'Thanh Khê' },
      { value: 'sontra', label: 'Sơn Trà' },
      { value: 'nguhanhson', label: 'Ngũ Hành Sơn' },
      { value: 'lienchieu', label: 'Liên Chiểu' },
      { value: 'hoavang', label: 'Hòa Vang' },
      { value: 'camle', label: 'Cẩm Lệ' },
    ],
  },
  'haiphong': {
    name: 'Hải Phòng',
    districts: [
      { value: 'hongbang', label: 'Hồng Bàng' },
      { value: 'lechan', label: 'Lê Chân' },
      { value: 'ngoquyen', label: 'Ngô Quyền' },
      { value: 'kienan', label: 'Kiến An' },
      { value: 'haian', label: 'Hải An' },
      { value: 'duongkinh', label: 'Dương Kinh' },
      { value: 'doanlu', label: 'Đồ Sơn' },
    ],
  },
  'cantho': {
    name: 'Cần Thơ',
    districts: [
      { value: 'ninhkieu', label: 'Ninh Kiều' },
      { value: 'binhthuy', label: 'Bình Thủy' },
      { value: 'cairang', label: 'Cái Răng' },
      { value: 'omon', label: 'Ô Môn' },
      { value: 'thotnot', label: 'Thốt Nốt' },
      { value: 'phongdien', label: 'Phong Điền' },
      { value: 'coodo', label: 'Cờ Đỏ' },
    ],
  },
  'bacninh': {
    name: 'Bắc Ninh',
    districts: [
      { value: 'bacninh', label: 'Bắc Ninh' },
      { value: 'tiendu', label: 'Tiên Du' },
      { value: 'thuanthanh', label: 'Thuận Thành' },
      { value: 'giaBinh', label: 'Gia Bình' },
      { value: 'luongtai', label: 'Lương Tài' },
      { value: 'quevo', label: 'Quế Võ' },
      { value: 'yenyen', label: 'Yên Phong' },
    ],
  },
};

const PROVINCE_OPTIONS = Object.entries(PROVINCES).map(([slug, data]) => ({
  value: slug,
  label: data.name,
}));

export default function PredictionForm({ onSubmit, loading }: PredictionFormProps) {
  const [features, setFeatures] = useState<PropertyFeatures>({
    area_m2: 90,
    bedroom_count: 2,
    bathroom_count: 2,
    floor_count: 1,
    property_type: 'apartment',
    listing_type: 'sell',
    province_slug: 'hanoi',
    district_slug: 'dongda',
  });

  function updateField(name: keyof PropertyFeatures, value: string) {
    const numericFields = ['area_m2', 'bedroom_count', 'bathroom_count', 'floor_count', 'front_width_m', 'road_width_m'];
    
    // When province changes, reset district to first available
    if (name === 'province_slug') {
      const newProvince = value as string;
      const firstDistrict = PROVINCES[newProvince]?.districts[0]?.value || '';
      setFeatures((current) => ({
        ...current,
        province_slug: newProvince,
        district_slug: firstDistrict,
      }));
      return;
    }

    setFeatures((current) => ({
      ...current,
      [name]: numericFields.includes(name) ? (value === '' ? undefined : Number(value)) : value || undefined,
    }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(features);
  }

  const currentProvince = features.province_slug as string;
  const districtOptions = PROVINCES[currentProvince]?.districts || [];

  return (
    <form onSubmit={submit} className="panel space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Prediction Input</h2>
        <p className="mt-1 text-sm text-slate-600">Property details used by the model.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Area (m²)" type="number" value={features.area_m2 ?? ''} onChange={(value) => updateField('area_m2', value)} />
        <Field label="Bedrooms" type="number" value={features.bedroom_count ?? ''} onChange={(value) => updateField('bedroom_count', value)} />
        <Field label="Bathrooms" type="number" value={features.bathroom_count ?? ''} onChange={(value) => updateField('bathroom_count', value)} />
        <Field label="Floors" type="number" value={features.floor_count ?? ''} onChange={(value) => updateField('floor_count', value)} />
        <Select label="Property Type" value={features.property_type || ''} onChange={(value) => updateField('property_type', value)} options={['apartment', 'house', 'land', 'villa']} />
        <Select label="Listing Type" value={features.listing_type || ''} onChange={(value) => updateField('listing_type', value)} options={['sell', 'rent']} />
        <SelectDropdown 
          label="Province" 
          value={features.province_slug || ''} 
          onChange={(value) => updateField('province_slug', value)} 
          options={PROVINCE_OPTIONS}
        />
        <SelectDropdown 
          label="District" 
          value={features.district_slug || ''} 
          onChange={(value) => updateField('district_slug', value)} 
          options={districtOptions}
        />
      </div>

      <label className="block">
        <span className="label">Description</span>
        <textarea
          className="input min-h-24 resize-y"
          value={features.description || ''}
          onChange={(event) => updateField('description', event.target.value)}
          placeholder="Near main road, full furniture, legal clear..."
        />
      </label>

      <button type="submit" disabled={loading} className="inline-flex w-full items-center justify-center gap-2 rounded bg-cyan-700 px-4 py-3 font-semibold text-white hover:bg-cyan-800 disabled:cursor-not-allowed disabled:opacity-60">
        <Calculator className="h-5 w-5" />
        {loading ? 'Predicting...' : 'Predict Price'}
      </button>
    </form>
  );
}

function Field({ label, value, onChange, type = 'text' }: { label: string; value: string | number; onChange: (value: string) => void; type?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input className="input" type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <select className="input" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function SelectDropdown({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: { value: string; label: string }[] }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <select className="input" value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">-- Select {label} --</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
