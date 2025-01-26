create table if not exists days(
    day date primary key,
    is_working boolean default false
);

create table if not exists users(
    id bigint primary key,
    username text,
    full_name text
);

create table if not exists slots(
    id serial primary key,
    datetime timestamp unique,
    is_booked boolean default false
);

create table appointments(
    id serial primary key,
    user_id bigint references users(id),
    start_time timestamp,
    end_time timestamp,
    comment text
);