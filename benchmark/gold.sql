-- d01
SELECT COUNT(*) FROM Customer WHERE Country = 'Germany';

-- d02
SELECT COUNT(*) FROM Track WHERE Milliseconds > 600000;

-- d03
SELECT ROUND(SUM(Total), 2) FROM Invoice WHERE strftime('%Y', InvoiceDate) = '2013';

-- d04
SELECT FirstName, LastName FROM Employee ORDER BY HireDate LIMIT 1;

-- d05
SELECT COUNT(*) FROM Track WHERE Composer IS NULL;

-- d06
SELECT ar.Name
FROM Artist ar
JOIN Album al ON al.ArtistId = ar.ArtistId
JOIN Track t ON t.AlbumId = al.AlbumId
JOIN InvoiceLine il ON il.TrackId = t.TrackId
GROUP BY ar.ArtistId
ORDER BY SUM(il.UnitPrice * il.Quantity) DESC
LIMIT 5;

-- d07
SELECT e.FirstName, e.LastName, ROUND(SUM(i.Total), 2)
FROM Employee e
JOIN Customer c ON c.SupportRepId = e.EmployeeId
JOIN Invoice i ON i.CustomerId = c.CustomerId
WHERE e.Title = 'Sales Support Agent'
GROUP BY e.EmployeeId;

-- d08
SELECT m.Name, COUNT(*)
FROM MediaType m
JOIN Track t ON t.MediaTypeId = m.MediaTypeId
GROUP BY m.MediaTypeId;

-- d09
SELECT al.Title, ar.Name
FROM Album al
JOIN Artist ar ON ar.ArtistId = al.ArtistId
JOIN Track t ON t.AlbumId = al.AlbumId
GROUP BY al.AlbumId
ORDER BY COUNT(*) DESC
LIMIT 1;

-- d10
SELECT c.FirstName, c.LastName
FROM Customer c
JOIN Invoice i ON i.CustomerId = c.CustomerId
GROUP BY c.CustomerId
ORDER BY SUM(i.Total) DESC
LIMIT 5;

-- d11
SELECT SUM(il.Quantity)
FROM InvoiceLine il
JOIN Track t ON t.TrackId = il.TrackId
JOIN Genre g ON g.GenreId = t.GenreId
WHERE g.Name = 'Rock';

-- d12
SELECT ROUND(SUM(i.Total), 2)
FROM Customer c
JOIN Invoice i ON i.CustomerId = c.CustomerId
WHERE c.City = 'Montréal';

-- d13
SELECT ROUND(SUM(Total), 2) FROM Invoice WHERE BillingCity = 'São Paulo';

-- d14
SELECT COUNT(*)
FROM Track t
JOIN MediaType m ON m.MediaTypeId = t.MediaTypeId
WHERE m.Name = 'Purchased AAC audio file';

-- d15
SELECT COUNT(*)
FROM Track t
JOIN Genre g ON g.GenreId = t.GenreId
WHERE g.Name = 'Sci Fi & Fantasy';
