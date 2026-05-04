import { FormEvent, useState } from 'react';
import { Calculator } from 'lucide-react';
import { PropertyFeatures } from '../api/client';

interface PredictionFormProps {
  onSubmit: (features: PropertyFeatures) => void;
  loading: boolean;
}

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
    setFeatures((current) => ({
      ...current,
      [name]: numericFields.includes(name) ? (value === '' ? undefined : Number(value)) : value || undefined,
    }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(features);
  }

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
        <Field label="Province" value={features.province_slug || ''} onChange={(value) => updateField('province_slug', value)} />
        <Field label="District" value={features.district_slug || ''} onChange={(value) => updateField('district_slug', value)} />
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
